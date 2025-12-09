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

# ------------------ Core Logic Functions (not Flask Responses) ------------------
def logic_get_identity():
    identity = sga.get_pccid()
    return {"success": True, "identity": identity}

def logic_get_cw(identity):
    if not identity:
        return {"success": False}
    iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
        credentials.cloudflaretokens,
        credentials.iotusername,
        credentials.iotpassword
    )
    cw, transactionid = sga.get_cyberrock_cw(
        credentials.cloudflaretokens,
        iotaccesstoken,
        identity,
        False
    )
    return {"success": True, "cw": cw, "transactionId": transactionid}

def logic_get_rw(cw):
    if not cw:
        return {"success": False}
    def intToList(number):
        from math import log, ceil
        L1 = log(number, 256)
        L2 = ceil(L1)
        if L1 == L2:
            L2 += 1
        return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]

    cw_int = int(cw, 16)
    cw_list = intToList(cw_int)
    rw = sga.do_rw_only(cw_list)
    return {"success": True, "rw": rw}

def logic_authenticate(identity, cw, rw, transaction_id):
    if not all([identity, cw, rw, transaction_id]):
        return {"success": False}
    iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
        credentials.cloudflaretokens, credentials.iotusername, credentials.iotpassword
    )
    sga.do_submit_rw(credentials.cloudflaretokens, iotaccesstoken, identity, cw, rw, transaction_id, False)
    auth_result, claim_id = sga.do_retrieve_result(credentials.cloudflaretokens, iotaccesstoken, transaction_id, False)
    success = auth_result in ['CLAIM_ID', 'AUTH_OK']
    return {"success": success}

# ------------------ Flask Endpoints ------------------
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({"success": True})

@app.route('/api/get-identity', methods=['GET'])
def api_get_identity():
    try:
        set_led_status('yellow')
        res = logic_get_identity()
        set_led_status('green')
        return jsonify(res)
    except:
        set_led_status('red')
        return jsonify({"success": False})

@app.route('/api/get-cw', methods=['POST'])
def api_get_cw():
    try:
        set_led_status('yellow')
        data = request.get_json()
        res = logic_get_cw(data.get('identity'))
        set_led_status('green')
        return jsonify(res)
    except:
        set_led_status('red')
        return jsonify({"success": False})

@app.route('/api/get-rw', methods=['POST'])
def api_get_rw():
    try:
        set_led_status('yellow')
        data = request.get_json()
        res = logic_get_rw(data.get('cw'))
        set_led_status('green')
        return jsonify(res)
    except:
        set_led_status('red')
        return jsonify({"success": False})

@app.route('/api/authenticate', methods=['POST'])
def api_authenticate():
    try:
        set_led_status('yellow')
        data = request.get_json()
        res = logic_authenticate(
            data.get('identity'),
            data.get('cw'),
            data.get('rw'),
            data.get('transactionId')
        )
        set_led_status('green')
        return jsonify(res)
    except:
        set_led_status('red')
        return jsonify({"success": False})

# ------------------ MQTT Integration ------------------
DEVICE_ID = os.getenv("DEVICE_NAME", "Pi-Default")
BROKER = "54.255.173.75"

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        fn = payload.get("functionName")
        args = payload.get("args", [{}])[0]  # default empty dict

        print(f"Received MQTT command: {fn}, args: {args}")

        # Map function names to logic functions
        logic_map = {
            "get_identity": lambda _: logic_get_identity(),
            "get_cw": lambda d: logic_get_cw(d.get('identity')),
            "get_rw": lambda d: logic_get_rw(d.get('cw')),
            "authenticate": lambda d: logic_authenticate(d.get('identity'), d.get('cw'), d.get('rw'), d.get('transactionId')),
            "status": lambda _: {"success": True}
        }

        if fn in logic_map:
            result = logic_map[fn](args)
        else:
            result = {"success": False}

        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(result))
        print(f"Sent MQTT response: {result}")

    except Exception as e:
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps({"success": False}))
        print(f"MQTT error: {e}")

def run_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, 1883, 60)
    client.loop_forever()

# Start MQTT in separate thread
threading.Thread(target=run_mqtt, daemon=True).start()

# ------------------ Run Flask ------------------
if __name__ == "__main__":
    print("Starting Pi API Server with MQTT...")
    app.run(host="0.0.0.0", port=8000, debug=True)
