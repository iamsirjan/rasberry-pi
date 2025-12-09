import json
import threading
import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request
from flask_cors import CORS
import sys

# ------------------ Import your SandGrain modules ------------------
sys.path.insert(1, '/home/pi/SandGrain/SandGrainSuite_USB/')
try:
    import sga as sga
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

# ------------------ Flask Endpoints ------------------
@app.route('/api/status', methods=['GET'])
def api_status():
    return jsonify({'status': 'ok', 'message': 'Raspberry Pi API is running'})

@app.route('/api/get-identity', methods=['GET'])
def get_identity():
    try:
        set_led_status('yellow')
        identity = sga.get_pccid()
        set_led_status('green')
        return jsonify({'success': True, 'identity': identity})
    except Exception as e:
        set_led_status('red')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-cw', methods=['POST'])
def get_cw():
    try:
        set_led_status('yellow')
        data = request.get_json()
        identity = data.get('identity')
        if not identity:
            return jsonify({'success': False, 'error': 'Identity is required'}), 400
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
        set_led_status('green')
        return jsonify({'success': True, 'cw': cw, 'transactionId': transactionid})
    except Exception as e:
        set_led_status('red')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-rw', methods=['POST'])
def get_rw():
    try:
        set_led_status('yellow')
        data = request.get_json()
        cw = data.get('cw')
        if not cw:
            return jsonify({'success': False, 'error': 'CW is required'}), 400

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
        set_led_status('green')
        return jsonify({'success': True, 'rw': rw})
    except Exception as e:
        set_led_status('red')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    try:
        set_led_status('yellow')
        data = request.get_json()
        identity = data.get('identity')
        cw = data.get('cw')
        rw = data.get('rw')
        transaction_id = data.get('transactionId')
        if not all([identity, cw, rw, transaction_id]):
            return jsonify({'success': False, 'error': 'All parameters required'}), 400

        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens, credentials.iotusername, credentials.iotpassword
        )
        sga.do_submit_rw(credentials.cloudflaretokens, iotaccesstoken, identity, cw, rw, transaction_id, False)
        auth_result, claim_id = sga.do_retrieve_result(credentials.cloudflaretokens, iotaccesstoken, transaction_id, False)
        set_led_status('green' if auth_result in ['CLAIM_ID', 'AUTH_OK'] else 'red')
        return jsonify({'success': auth_result in ['CLAIM_ID', 'AUTH_OK'], 'authResult': auth_result, 'claimId': claim_id})
    except Exception as e:
        set_led_status('red')
        return jsonify({'success': False, 'error': str(e)}), 500

# ------------------ MQTT Integration ------------------
DEVICE_ID = "pi_001"
BROKER = "54.255.173.75"

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    client.subscribe(f"pi/{DEVICE_ID}/command")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        function_name = payload.get("functionName")
        args = payload.get("args", [])
        print(f"Received MQTT command: {function_name}, args: {args}")

        command_map = {
            "status": api_status,
            "get_identity": get_identity,
            "get_cw": get_cw,
            "get_rw": get_rw,
            "authenticate": authenticate
        }

        with app.app_context():  # FIX: ensures Flask context
            if function_name in command_map:
                if function_name in ["get_cw", "get_rw", "authenticate"]:
                    class DummyRequest:
                        def get_json(self_inner):
                            return args[0] if args else {}
                    response = command_map[function_name](DummyRequest())
                else:
                    response = command_map[function_name]()
                payload_response = response.get_json() if hasattr(response, 'get_json') else response
            else:
                payload_response = {"error": f"Function {function_name} not found"}

        client.publish(f"pi/{DEVICE_ID}/response", json.dumps(payload_response))
        print(f"Sent MQTT response: {payload_response}")
    except Exception as e:
        client.publish(f"pi/{DEVICE_ID}/response", json.dumps({"error": str(e)}))
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
