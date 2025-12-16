import json
import threading
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_cors import CORS
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# ------------------ Import SandGrain modules ------------------
sys.path.insert(1, '/home/pi/SandGrain/SandGrainSuite_USB/')
try:
    import sga
    import SandGrain_Credentials as credentials
except ImportError:
    print("Modules not found, using mock implementations")

    class MockSGA:
        def get_pccid(self):
            return "mock_pccid_123456"

        def do_cyberrock_iot_login(self, tokens, username, password):
            return "mock_token", "mock_iotid"

        def get_cyberrock_cw(self, tokens, accesstoken, pccid, request_sig):
            return "mock_cw_abcdef", "mock_transaction_id"

        def do_rw_only(self, cw_list):
            return "mock_rw_123456"

        def do_submit_rw(self, tokens, accesstoken, pccid, cw, rw, transactionid, request_sig):
            return "mock_response_transaction_id"

        def do_retrieve_result(self, tokens, accesstoken, transactionid, request_sig):
            return "AUTH_OK", "mock_claim_id"

    sga = MockSGA()

    class MockCredentials:
        cloudflaretokens = {'CF-Access-Client-Id': 'test', 'CF-Access-Client-Secret': 'test'}
        iotusername = 'test'
        iotpassword = 'test'

    credentials = MockCredentials()

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
        if status == 'green':
            GPIO.output(5, GPIO.HIGH)
            GPIO.output(6, GPIO.LOW)
            GPIO.output(12, GPIO.LOW)
        elif status == 'red':
            GPIO.output(5, GPIO.LOW)
            GPIO.output(6, GPIO.HIGH)
            GPIO.output(12, GPIO.LOW)
        elif status == 'yellow':
            GPIO.output(5, GPIO.LOW)
            GPIO.output(6, GPIO.LOW)
            GPIO.output(12, GPIO.HIGH)
    else:
        print(f"Mock LED status: {status}")

# ------------------ Logic Functions ------------------
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
        return {"success": False, "error": str(e)}

def get_cw_logic(identity):
    set_led_status('yellow')
    try:
        if not identity:
            raise ValueError("Identity is required")
        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
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
        return {"success": False, "error": str(e)}

def get_rw_logic(cw):
    set_led_status('yellow')
    try:
        if not cw:
            raise ValueError("CW is required")

        from math import log, ceil
        def intToList(number):
            L1 = log(number, 256)
            L2 = ceil(L1)
            if L1 == L2:
                L2 += 1
            return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]

        cw_int = int(cw, 16)
        cw_list = intToList(cw_int)
        rw = sga.do_rw_only(cw_list)
        set_led_status('green')
        return {"success": True, "rw": rw}
    except Exception as e:
        set_led_status('red')
        return {"success": False, "error": str(e)}

def authenticate_logic(identity, cw, rw, transactionId):
    set_led_status('yellow')
    try:
        if not all([identity, cw, rw, transactionId]):
            raise ValueError("All parameters required")

        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens, credentials.iotusername, credentials.iotpassword
        )
        sga.do_submit_rw(credentials.cloudflaretokens, iotaccesstoken, identity, cw, rw, transactionId, False)
        auth_result, claim_id = sga.do_retrieve_result(credentials.cloudflaretokens, iotaccesstoken, transactionId, False)
        set_led_status('green' if auth_result in ['CLAIM_ID', 'AUTH_OK'] else 'red')
        return {"success": auth_result in ['CLAIM_ID', 'AUTH_OK'], "authResult": auth_result, "claimId": claim_id}
    except Exception as e:
        set_led_status('red')
        return {"success": False, "error": str(e)}

# ------------------ Flask Endpoints ------------------
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify(status_logic())

@app.route('/api/get-identity', methods=['GET'])
def get_identity():
    return jsonify(get_identity_logic())

@app.route('/api/get-cw', methods=['POST'])
def get_cw():
    data = request.get_json()
    return jsonify(get_cw_logic(data.get('identity')))

@app.route('/api/get-rw', methods=['POST'])
def get_rw():
    data = request.get_json()
    return jsonify(get_rw_logic(data.get('cw')))

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    data = request.get_json()
    return jsonify(authenticate_logic(
        data.get('identity'),
        data.get('cw'),
        data.get('rw'),
        data.get('transactionId')
    ))

# ------------------ MQTT Integration ------------------
DEVICE_ID = os.getenv("DEVICE_ID", "Pi-Default")
BROKER = "3.67.46.166"

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        function_name = payload.get("functionName")
        args = payload.get("args", [])

        if function_name == "status":
            response = status_logic()
        elif function_name == "get_identity":
            response = get_identity_logic()
        elif function_name == "get_cw":
            identity = args[0].get("identity") if args else None
            response = get_cw_logic(identity)
        elif function_name == "get_rw":
            cw = args[0].get("cw") if args else None
            response = get_rw_logic(cw)
        elif function_name == "authenticate":
            params = args[0] if args else {}
            response = authenticate_logic(
                params.get("identity"),
                params.get("cw"),
                params.get("rw"),
                params.get("transactionId")
            )
        else:
            response = {"success": False, "error": f"Function {function_name} not found"}

        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(response))
        print(f"Sent MQTT response: {response}")
    except Exception as e:
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps({"success": False, "error": str(e)}))
        print(f"MQTT error: {e}")

def run_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, 1883, 60)
    client.loop_forever()

# Start MQTT in a separate thread
threading.Thread(target=run_mqtt, daemon=True).start()

# ------------------ Run Flask ------------------
if __name__ == "__main__":
    print("Starting Pi API Server with MQTT...")
    app.run(host="0.0.0.0", port=8000, debug=True)
