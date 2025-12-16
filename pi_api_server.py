import json
import threading
import time
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_cors import CORS
import sys
import os
from dotenv import load_dotenv
from queue import Queue
import uuid

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

# ------------------ SERIAL COMMAND QUEUE (CRITICAL FIX) ------------------
command_queue = Queue()
response_map = {}

def serial_worker():
    while True:
        job_id, fn, payload = command_queue.get()
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
        except Exception as e:
            result = {"success": False, "error": str(e)}

        response_map[job_id] = result
        command_queue.task_done()

# Start ONE serial worker only
threading.Thread(target=serial_worker, daemon=True).start()

# ------------------ Logic Functions (UNCHANGED) ------------------
def status_logic():
    return {"status": "ok", "message": "Raspberry Pi API is running"}

def get_identity_logic():
    set_led_status('yellow')
    identity = sga.get_pccid()
    set_led_status('green')
    return {"success": True, "identity": identity}

def get_cw_logic(identity):
    if not identity:
        raise ValueError("Identity is required")

    set_led_status('yellow')
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

def get_rw_logic(cw):
    if not cw:
        raise ValueError("CW is required")

    set_led_status('yellow')
    cw_int = int(cw, 16)
    rw = sga.do_rw_only(sga.intToList(cw_int))
    set_led_status('green')
    return {"success": True, "rw": rw}

def authenticate_logic(identity, cw, rw, transactionId):
    if not all([identity, cw, rw, transactionId]):
        raise ValueError("All parameters required")

    set_led_status('yellow')
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

# ------------------ Helper to enqueue calls ------------------
def enqueue_call(fn, payload):
    job_id = str(uuid.uuid4())
    command_queue.put((job_id, fn, payload))
    while job_id not in response_map:
        time.sleep(0.01)
    return response_map.pop(job_id)

# ------------------ Flask Endpoints ------------------
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify(enqueue_call("status", {}))

@app.route('/api/get-identity', methods=['GET'])
def api_get_identity():
    return jsonify(enqueue_call("get_identity", {}))

@app.route('/api/get-cw', methods=['POST'])
def api_get_cw():
    data = request.get_json()
    return jsonify(enqueue_call("get_cw", {"identity": data.get("identity")}))

@app.route('/api/get-rw', methods=['POST'])
def api_get_rw():
    data = request.get_json()
    return jsonify(enqueue_call("get_rw", {"cw": data.get("cw")}))

@app.route('/api/authenticate', methods=['POST'])
def api_authenticate():
    data = request.get_json()
    return jsonify(enqueue_call("authenticate", data))

# ------------------ MQTT Integration ------------------
DEVICE_ID = os.getenv("DEVICE_ID", "Pi-Default")
BROKER = "3.67.46.166"

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        fn = payload.get("functionName")
        args = payload.get("args", [{}])
        response = enqueue_call(fn, args[0])
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(response))
    except Exception as e:
        client.publish(
            f"pi/{DEVICE_ID}/response",
            json.dumps({"success": False, "error": str(e)})
        )

def run_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, 1883, 60)
    client.loop_forever()

threading.Thread(target=run_mqtt, daemon=True).start()

# ------------------ Run Flask ------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Starting Pi API Server with SERIAL QUEUE + MQTT")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
