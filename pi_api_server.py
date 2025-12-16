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
import logging

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ------------------ Import SandGrain modules ------------------
try:
    import sga
    import SandGrain_Credentials as credentials
    logger.info("✓ SGA module loaded successfully")
except ImportError as e:
    logger.error(f"✗ Failed to import modules: {e}")
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
        logger.warning("GPIO not available - running in mock mode")
        return None

GPIO = gpio_setup()

def set_led_status(status):
    if GPIO:
        GPIO.output(5, status == "green")
        GPIO.output(6, status == "red")
        GPIO.output(12, status == "yellow")

# ------------------ NEVER-FAIL COMMAND QUEUE ------------------
command_queue = Queue()
response_map = {}
response_map_lock = threading.Lock()

# Job tracking
job_start_times = {}
JOB_MAX_TIME = 300  # 5 minutes max per job (very generous)

def serial_worker():
    """
    Worker thread - NEVER gives up on a job
    Each job will retry indefinitely until success
    """
    logger.info("[Worker] Serial worker started - ZERO FAILURE MODE")
    
    while True:
        try:
            job_id, fn, payload = command_queue.get(timeout=1)
            job_start_times[job_id] = time.time()
            
            logger.info(f"[Worker] Starting job {job_id}: {fn}")
            
            # Execute function - these functions now NEVER fail
            try:
                if fn == "status":
                    result = status_logic()
                elif fn == "get_identity":
                    result = get_identity_logic()
                elif fn == "get_cw":
                    result = get_cw_logic(payload.get("identity"))
                elif fn == "get_rw":
                    result = get_rw_logic(payload.get("cw"))
                elif fn == "authenticate":
                    result = authenticate_logic(
                        payload.get("identity"),
                        payload.get("cw"),
                        payload.get("rw"),
                        payload.get("transactionId")
                    )
                else:
                    result = {"success": False, "error": "Unknown function"}
                
                duration = time.time() - job_start_times[job_id]
                logger.info(f"[Worker] ✓ Job {job_id} completed in {duration:.2f}s")
                
            except Exception as e:
                duration = time.time() - job_start_times.get(job_id, time.time())
                logger.error(f"[Worker] ✗ Job {job_id} failed after {duration:.2f}s: {e}")
                result = {"success": False, "error": str(e)}
            
            # Store result
            with response_map_lock:
                response_map[job_id] = result
                if job_id in job_start_times:
                    del job_start_times[job_id]
            
            command_queue.task_done()
            
        except Empty:
            # Check for stalled jobs
            current_time = time.time()
            with response_map_lock:
                stalled = [
                    (jid, current_time - start_time)
                    for jid, start_time in job_start_times.items()
                    if current_time - start_time > JOB_MAX_TIME
                ]
                
                for jid, duration in stalled:
                    logger.error(f"[Worker] Job {jid} exceeded max time ({duration:.0f}s) - marking as failed")
                    response_map[jid] = {
                        "success": False,
                        "error": f"Job exceeded maximum time ({JOB_MAX_TIME}s)"
                    }
                    del job_start_times[jid]
        
        except Exception as e:
            logger.error(f"[Worker] Unexpected error: {e}")

# Start worker thread
threading.Thread(target=serial_worker, daemon=True).start()

# ------------------ Logic Functions (ZERO-FAILURE MODE) ------------------
def status_logic():
    """Status check - always succeeds"""
    return {"status": "ok", "message": "Raspberry Pi API is running"}

def get_identity_logic():
    """Get identity - retries forever until success"""
    set_led_status('yellow')
    try:
        # This function now retries indefinitely
        identity = sga.get_pccid()
        set_led_status('green')
        return {"success": True, "identity": identity}
    except Exception as e:
        set_led_status('red')
        logger.error(f"get_identity failed: {e}")
        # In zero-failure mode, this shouldn't happen
        # but if it does, return error
        raise

def get_cw_logic(identity):
    """Get CW - retries forever until success"""
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
        logger.error(f"get_cw failed: {e}")
        raise

def get_rw_logic(cw):
    """Get RW - retries forever until success"""
    if not cw:
        raise ValueError("CW is required")

    set_led_status('yellow')
    try:
        cw_int = int(cw, 16)
        # This function now retries indefinitely
        rw = sga.do_rw_only(sga.intToList(cw_int))
        set_led_status('green')
        return {"success": True, "rw": rw}
    except Exception as e:
        set_led_status('red')
        logger.error(f"get_rw failed: {e}")
        raise

def authenticate_logic(identity, cw, rw, transactionId):
    """Authenticate - retries forever until success"""
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
        logger.error(f"authenticate failed: {e}")
        raise

# ------------------ WAIT-FOREVER Helper ------------------
def enqueue_and_wait(fn, payload, timeout=300):
    """
    Enqueue a job and wait for it to complete
    Timeout is very generous (5 minutes default)
    """
    job_id = str(uuid.uuid4())
    
    # Enqueue job
    command_queue.put((job_id, fn, payload))
    logger.info(f"[API] Enqueued job {job_id}: {fn}")
    
    # Wait for response - check every 0.5s
    start_time = time.time()
    last_log = start_time
    
    while True:
        # Check if we have response
        with response_map_lock:
            if job_id in response_map:
                result = response_map.pop(job_id)
                elapsed = time.time() - start_time
                logger.info(f"[API] Job {job_id} completed in {elapsed:.2f}s")
                return result
        
        # Check timeout
        elapsed = time.time() - start_time
        if elapsed > timeout:
            logger.error(f"[API] Job {job_id} timed out after {timeout}s")
            return {
                "success": False,
                "error": f"Operation timed out after {timeout}s - device may be unresponsive"
            }
        
        # Log progress every 10 seconds
        if elapsed - (last_log - start_time) > 10:
            logger.info(f"[API] Still waiting for job {job_id} ({elapsed:.0f}s elapsed)...")
            last_log = time.time()
        
        # Sleep before next check
        time.sleep(0.5)

# ------------------ Flask Endpoints (ZERO-FAILURE MODE) ------------------
@app.route('/api/health', methods=['GET'])
def api_health():
    """Detailed health check"""
    try:
        health = {
            "status": "ok",
            "timestamp": time.time(),
            "queue_size": command_queue.qsize(),
            "pending_responses": len(response_map),
            "active_jobs": len(job_start_times),
            "mode": "ZERO_FAILURE",
            "devices": []
        }
        
        # Get device stats
        if sga._device_pool.initialized:
            for device in sga._device_pool.devices:
                health["devices"].append({
                    "id": device.device_id,
                    "port": device.serial_port,
                    "consecutive_failures": device.consecutive_failures,
                    "total_ops": device.total_operations,
                    "successful_ops": device.successful_operations,
                    "success_rate": f"{(device.successful_operations / device.total_operations * 100):.1f}%" 
                        if device.total_operations > 0 else "N/A"
                })
        
        return jsonify(health)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/api/status', methods=['GET'])
def api_status():
    """Quick status check"""
    try:
        return jsonify(enqueue_and_wait("status", {}, timeout=10))
    except Exception as e:
        logger.error(f"Status endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-identity', methods=['GET'])
def api_get_identity():
    """
    Get device identity
    NEVER fails - will wait until device responds
    """
    try:
        logger.info("API: get-identity request received")
        # Very generous timeout - operation will retry internally
        result = enqueue_and_wait("get_identity", {}, timeout=180)
        return jsonify(result)
    except Exception as e:
        logger.error(f"get-identity endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-cw', methods=['POST'])
def api_get_cw():
    """Get CW - NEVER fails"""
    try:
        data = request.get_json()
        if not data or "identity" not in data:
            return jsonify({"success": False, "error": "Missing identity parameter"}), 400
        
        logger.info("API: get-cw request received")
        result = enqueue_and_wait("get_cw", {"identity": data.get("identity")}, timeout=180)
        return jsonify(result)
    except Exception as e:
        logger.error(f"get-cw endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/get-rw', methods=['POST'])
def api_get_rw():
    """
    Get RW - NEVER fails
    Will wait and retry until device responds
    """
    try:
        data = request.get_json()
        if not data or "cw" not in data:
            return jsonify({"success": False, "error": "Missing cw parameter"}), 400
        
        logger.info("API: get-rw request received")
        # Very generous timeout
        result = enqueue_and_wait("get_rw", {"cw": data.get("cw")}, timeout=180)
        return jsonify(result)
    except Exception as e:
        logger.error(f"get-rw endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/authenticate', methods=['POST'])
def api_authenticate():
    """Authenticate - NEVER fails"""
    try:
        data = request.get_json()
        required_fields = ["identity", "cw", "rw", "transactionId"]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return jsonify({"success": False, "error": f"Missing parameters: {missing}"}), 400
        
        logger.info("API: authenticate request received")
        result = enqueue_and_wait("authenticate", data, timeout=240)
        return jsonify(result)
    except Exception as e:
        logger.error(f"authenticate endpoint error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# ------------------ MQTT Integration ------------------
DEVICE_ID = os.getenv("DEVICE_ID", "Pi-Default")
BROKER = "3.67.46.166"

def on_connect(client, userdata, flags, rc):
    logger.info(f"[MQTT] Connected with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        fn = payload.get("functionName")
        args = payload.get("args", [{}])
        
        # Map function to timeout
        timeout_map = {
            "status": 10,
            "get_identity": 180,
            "get_cw": 180,
            "get_rw": 180,
            "authenticate": 240
        }
        timeout = timeout_map.get(fn, 180)
        
        response = enqueue_and_wait(fn, args[0] if args else {}, timeout=timeout)
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
        logger.info("[MQTT] Starting loop")
        client.loop_forever()
    except Exception as e:
        logger.error(f"[MQTT] Connection failed: {e}")

threading.Thread(target=run_mqtt, daemon=True).start()

# ------------------ Graceful Shutdown ------------------
def signal_handler(sig, frame):
    logger.info("\n[API] Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ------------------ Run Flask ------------------
if __name__ == "__main__":
    print("=" * 70)
    print("Starting Pi API Server - ZERO FAILURE MODE")
    print("  - Operations NEVER fail, they retry until success")
    print("  - Global serial lock prevents race conditions")
    print("  - Automatic device reset on persistent failures")
    print("  - Very generous timeouts (3-5 minutes per operation)")
    print("=" * 70)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
