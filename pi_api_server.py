import json
import threading
import time
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_cors import CORS
import sys
import os
from dotenv import load_dotenv
from queue import Queue, Empty
import uuid
import signal

load_dotenv()

# ------------------ Import SandGrain modules ------------------
try:
    import sga
    import SandGrain_Credentials as credentials
    print("✓ SGA module loaded successfully")
except ImportError as e:
    print(f"✗ Failed to import modules: {e}")
    sys.exit(1)

# ------------------ Flask App ------------------
app = Flask(__name__)
CORS(app)

# ------------------ GPIO / LED Setup ------------------
def gpio_setup():
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(5, GPIO.OUT)   # Green
        GPIO.setup(6, GPIO.OUT)   # Red
        GPIO.setup(12, GPIO.OUT)  # Yellow
        GPIO.output(5, GPIO.LOW)
        GPIO.output(6, GPIO.LOW)
        GPIO.output(12, GPIO.HIGH)
        return GPIO
    except ImportError:
        print("GPIO not available - running in mock mode")
        return None

GPIO = gpio_setup()

def set_led_status(status):
    if GPIO:
        GPIO.output(5, status == "green")
        GPIO.output(6, status == "red")
        GPIO.output(12, status == "yellow")
    else:
        print(f"Mock LED status: {status}")

# ------------------ IMPROVED SERIAL COMMAND QUEUE ------------------
command_queue = Queue()
response_map = {}
response_map_lock = threading.Lock()

# Job timeout tracking
job_timeouts = {}
JOB_TIMEOUT = 30  # 30 seconds max per job

def serial_worker():
    """Worker thread that processes serial commands sequentially"""
    print("[Worker] Serial worker started")
    
    while True:
        try:
            job_id, fn, payload = command_queue.get(timeout=1)
            start_time = time.time()
            
            print(f"[Worker] Processing job {job_id}: {fn}")
            
            try:
                if fn == "status":
                    result = status_logic()
                elif fn == "get_identity":
                    result = get_identity_logic()
                elif fn == "get_cw":
                    result = get_cw_logic(payload["identity"])
                elif fn == "get_rw":
                    result = get_rw_logic(payload["cw"])
                elif fn == "authenticate":
                    result = authenticate_logic(
                        payload["identity"],
                        payload["cw"],
                        payload["rw"],
                        payload["transactionId"]
                    )
                else:
                    result = {"success": False, "error": "Unknown function"}
                    
                duration = time.time() - start_time
                print(f"[Worker] Job {job_id} completed in {duration:.2f}s")
                
            except Exception as e:
                duration = time.time() - start_time
                print(f"[Worker] Job {job_id} failed after {duration:.2f}s: {e}")
                result = {"success": False, "error": str(e)}
            
            # Store result
            with response_map_lock:
                response_map[job_id] = result
            
            command_queue.task_done()
            
        except Empty:
            # Timeout waiting for job - check for expired jobs
            current_time = time.time()
            with response_map_lock:
                expired_jobs = [jid for jid, timeout_time in job_timeouts.items() 
                               if current_time > timeout_time and jid not in response_map]
                for jid in expired_jobs:
                    response_map[jid] = {
                        "success": False, 
                        "error": "Job timeout - device did not respond in time"
                    }
                    del job_timeouts[jid]
                    print(f"[Worker] Job {jid} timed out")
        except Exception as e:
            print(f"[Worker] Unexpected error: {e}")

# Start serial worker thread
threading.Thread(target=serial_worker, daemon=True).start()

# ------------------ Logic Functions with Error Handling ------------------
def status_logic():
    return {"status": "ok", "message": "Raspberry Pi API is running"}

def get_identity_logic():
    set_led_status('yellow')
    try:
        identity = sga.get_pccid()
        set_led_status('green')
        return {"success": True, "identity": identity}
    except Exception as e:
        set_led_status('red')
        raise e

def get_cw_logic(identity):
    if not identity:
        raise ValueError("Identity is required")

    set_led_status('yellow')
    try:
        iotaccesstoken, _ = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens,
            credentials.iotusername,
            credentials.iotpassword
        )
        cw, transactionId = sga.get_cyberrock_cw(
            credentials.cloudflaretokens,
            iotaccesstoken,
            identity,
            False
        )
        set_led_status('green')
        return {"success": True, "cw": cw, "transactionId": transactionId}
    except Exception as e:
        set_led_status('red')
        raise e

def get_rw_logic(cw):
    if not cw:
        raise ValueError("CW is required")

    set_led_status('yellow')
    try:
        cw_int = int(cw, 16)
        rw = sga.do_rw_only(sga.intToList(cw_int))
        set_led_status('green')
        return {"success": True, "rw": rw}
    except Exception as e:
        set_led_status('red')
        raise e

def authenticate_logic(identity, cw, rw, transactionId):
    if not all([identity, cw, rw, transactionId]):
        raise ValueError("All parameters required")

    set_led_status('yellow')
    try:
        iotaccesstoken, _ = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens,
            credentials.iotusername,
            credentials.iotpassword
        )
        sga.do_submit_rw(
            credentials.cloudflaretokens,
            iotaccesstoken,
            identity,
            cw,
            rw,
            transactionId,
            False
        )
        auth_result, claim_id = sga.do_retrieve_result(
            credentials.cloudflaretokens,
            iotaccesstoken,
            transactionId,
            False
        )
        set_led_status('green' if auth_result in ['CLAIM_ID', 'AUTH_OK'] else 'red')
        return {
            "success": auth_result in ['CLAIM_ID', 'AUTH_OK'],
            "authResult": auth_result,
            "claimId": claim_id
        }
    except Exception as e:
        set_led_status('red')
        raise e

# ------------------ Improved Helper to enqueue calls ------------------
def enqueue_call(fn, payload, timeout=25):
    """Enqueue a call and wait for response with timeout"""
    job_id = str(uuid.uuid4())
    
    # Register timeout
    with response_map_lock:
        job_timeouts[job_id] = time.time() + timeout
    
    # Enqueue job
    command_queue.put((job_id, fn, payload))
    print(f"[API] Enqueued job {job_id}: {fn}")
    
    # Wait for response with timeout
    start_time = time.time()
    poll_interval = 0.1
    
    while time.time() - start_time < timeout:
        with response_map_lock:
            if job_id in response_map:
                result = response_map.pop(job_id)
                if job_id in job_timeouts:
                    del job_timeouts[job_id]
                print(f"[API] Job {job_id} completed")
                return result
        
        time.sleep(poll_interval)
    
    # Timeout - clean up
    with response_map_lock:
        if job_id in job_timeouts:
            del job_timeouts[job_id]
    
    print(f"[API] Job {job_id} timed out after {timeout}s")
    return {
        "success": False, 
        "error": f"Request timeout after {timeout} seconds"
    }

# ------------------ Flask Endpoints with Timeout Protection ------------------
@app.route('/api/status', methods=['GET'])
def api_status():
    try:
        return jsonify(enqueue_call("status", {}, timeout=5))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-identity', methods=['GET'])
def api_get_identity():
    try:
        return jsonify(enqueue_call("get_identity", {}, timeout=15))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-cw', methods=['POST'])
def api_get_cw():
    try:
        data = request.get_json()
        if not data or "identity" not in data:
            return jsonify({"success": False, "error": "Missing identity parameter"}), 400
        return jsonify(enqueue_call("get_cw", {"identity": data.get("identity")}, timeout=20))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-rw', methods=['POST'])
def api_get_rw():
    try:
        data = request.get_json()
        if not data or "cw" not in data:
            return jsonify({"success": False, "error": "Missing cw parameter"}), 400
        return jsonify(enqueue_call("get_rw", {"cw": data.get("cw")}, timeout=20))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/authenticate', methods=['POST'])
def api_authenticate():
    try:
        data = request.get_json()
        required_fields = ["identity", "cw", "rw", "transactionId"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return jsonify({"success": False, "error": f"Missing parameters: {missing}"}), 400
        return jsonify(enqueue_call("authenticate", data, timeout=30))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ------------------ MQTT Integration with Error Handling ------------------
DEVICE_ID = os.getenv("DEVICE_ID", "Pi-Default")
BROKER = "3.67.46.166"

def on_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        fn = payload.get("functionName")
        args = payload.get("args", [{}])
        
        # Set appropriate timeout based on function
        timeout_map = {
            "status": 5,
            "get_identity": 15,
            "get_cw": 20,
            "get_rw": 20,
            "authenticate": 30
        }
        timeout = timeout_map.get(fn, 25)
        
        response = enqueue_call(fn, args[0] if args else {}, timeout=timeout)
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(response))
    except Exception as e:
        error_response = {"success": False, "error": str(e)}
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(error_response))

def run_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(BROKER, 1883, 60)
        print("[MQTT] Starting loop")
        client.loop_forever()
    except Exception as e:
        print(f"[MQTT] Connection failed: {e}")

threading.Thread(target=run_mqtt, daemon=True).start()

# ------------------ Graceful Shutdown ------------------
def signal_handler(sig, frame):
    print("\n[API] Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ------------------ Run Flask ------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Starting Pi API Server with Improved Reliability")
    print("  - Serial queue with automatic retry")
    print("  - Request timeout protection")
    print("  - Better error handling")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
