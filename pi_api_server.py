from flask import Flask, jsonify, request
from flask_cors import CORS
import subprocess
import json
import sys
import os

# Add the path to your SandGrain modules
sys.path.insert(1, '/home/pi/SandGrain/SandGrainSuite_USB/')

# Import your modules
try:
    import sga as sga
    import SandGrain_Credentials as credentials
except ImportError as e:
    print(f"Import error: {e}")
    # Create mock functions for testing if modules aren't available
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

app = Flask(__name__)
CORS(app)  # Enable Cross-Origin Requests

# LED control functions
def gpio_setup():
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(5, GPIO.OUT)   # API_G
        GPIO.setup(6, GPIO.OUT)   # API_R
        GPIO.setup(12, GPIO.OUT)  # API_Y
        
        GPIO.output(5, GPIO.LOW)
        GPIO.output(6, GPIO.LOW)
        GPIO.output(12, GPIO.HIGH)
        return GPIO
    except ImportError:
        print("GPIO module not available - running in mock mode")
        return None

GPIO = gpio_setup()

def set_led_status(status):
    """Set LED status: green, red, or yellow"""
    if GPIO:
        if status == 'green':
            GPIO.output(5, GPIO.HIGH)  # Green on
            GPIO.output(6, GPIO.LOW)   # Red off
            GPIO.output(12, GPIO.LOW)  # Yellow off
        elif status == 'red':
            GPIO.output(5, GPIO.LOW)   # Green off
            GPIO.output(6, GPIO.HIGH)  # Red on
            GPIO.output(12, GPIO.LOW)  # Yellow off
        elif status == 'yellow':
            GPIO.output(5, GPIO.LOW)   # Green off
            GPIO.output(6, GPIO.LOW)   # Red off
            GPIO.output(12, GPIO.HIGH) # Yellow on
    else:
        print(f"Mock LED status: {status}")

@app.route('/api/status', methods=['GET'])
def api_status():
    """Check if API is running"""
    return jsonify({'status': 'ok', 'message': 'Raspberry Pi API is running'})

@app.route('/api/get-identity', methods=['GET'])
def get_identity():
    """Get the device identity (PCCID)"""
    try:
        set_led_status('yellow')
        identity = sga.get_pccid()
        set_led_status('green')
        return jsonify({
            'success': True,
            'identity': identity,
            'message': 'Identity retrieved successfully'
        })
    except Exception as e:
        set_led_status('red')
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to get identity'
        }), 500

@app.route('/api/get-cw', methods=['POST'])
def get_cw():
    """Get Challenge Word (CW) from CyberRock"""
    try:
        set_led_status('yellow')
        data = request.get_json()
        identity = data.get('identity')
        
        if not identity:
            return jsonify({
                'success': False,
                'error': 'Identity parameter is required'
            }), 400
        
        # Login to CyberRock
        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens, 
            credentials.iotusername, 
            credentials.iotpassword
        )
        
        # Get CW
        cw, transactionid = sga.get_cyberrock_cw(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            identity, 
            False
        )
        
        set_led_status('green')
        return jsonify({
            'success': True,
            'cw': cw,
            'transactionId': transactionid,
            'message': 'CW retrieved successfully'
        })
    except Exception as e:
        set_led_status('red')
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to get CW'
        }), 500

@app.route('/api/get-rw', methods=['POST'])
def get_rw():
    """Get Response Word (RW) from the device"""
    try:
        set_led_status('yellow')
        data = request.get_json()
        cw = data.get('cw')
        
        if not cw:
            return jsonify({
                'success': False,
                'error': 'CW parameter is required'
            }), 400
        
        # Convert CW to list format needed by do_rw_only
        def intToList(number):
            from math import log, ceil
            L1 = log(number, 256)
            L2 = ceil(L1)
            if L1 == L2:
                L2 += 1
            return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]
        
        cw_int = int(cw, 16)
        cw_list = intToList(cw_int)
        
        # Get RW from device
        rw = sga.do_rw_only(cw_list)
        
        set_led_status('green')
        return jsonify({
            'success': True,
            'rw': rw,
            'message': 'RW generated successfully'
        })
    except Exception as e:
        set_led_status('red')
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to get RW'
        }), 500

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    """Complete authentication process"""
    try:
        set_led_status('yellow')
        data = request.get_json()
        identity = data.get('identity')
        cw = data.get('cw')
        rw = data.get('rw')
        transaction_id = data.get('transactionId')
        
        if not all([identity, cw, rw, transaction_id]):
            return jsonify({
                'success': False,
                'error': 'Identity, CW, RW, and transactionId parameters are required'
            }), 400
        
        # Login to CyberRock
        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens, 
            credentials.iotusername, 
            credentials.iotpassword
        )
        
        # Submit RW
        transaction_response = sga.do_submit_rw(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            identity, 
            cw, 
            rw, 
            transaction_id, 
            False
        )
        
        # Get authentication result
        auth_result, claim_id = sga.do_retrieve_result(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            transaction_id, 
            False
        )
        
        # Set LED based on result
        if auth_result in ['CLAIM_ID', 'AUTH_OK']:
            set_led_status('green')
            success = True
        else:
            set_led_status('red')
            success = False
        
        return jsonify({
            'success': success,
            'authResult': auth_result,
            'claimId': claim_id,
            'message': f'Authentication result: {auth_result}'
        })
    except Exception as e:
        set_led_status('red')
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Authentication failed'
        }), 500

@app.route('/api/full-auth', methods=['GET'])
def full_authentication():
    """Run the complete authentication process in one call"""
    try:
        set_led_status('yellow')
        
        # Get identity
        identity = sga.get_pccid()
        
        # Login to CyberRock
        iotaccesstoken, iotid = sga.do_cyberrock_iot_login(
            credentials.cloudflaretokens, 
            credentials.iotusername, 
            credentials.iotpassword
        )
        
        # Get CW
        cw, transaction_id = sga.get_cyberrock_cw(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            identity, 
            False
        )
        
        # Convert CW to list format
        def intToList(number):
            from math import log, ceil
            L1 = log(number, 256)
            L2 = ceil(L1)
            if L1 == L2:
                L2 += 1
            return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]
        
        cw_int = int(cw, 16)
        cw_list = intToList(cw_int)
        
        # Get RW
        rw = sga.do_rw_only(cw_list)
        
        # Submit RW
        transaction_response = sga.do_submit_rw(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            identity, 
            cw, 
            rw, 
            transaction_id, 
            False
        )
        
        # Get result
        auth_result, claim_id = sga.do_retrieve_result(
            credentials.cloudflaretokens, 
            iotaccesstoken, 
            transaction_id, 
            False
        )
        
        # Set LED based on result
        if auth_result in ['CLAIM_ID', 'AUTH_OK']:
            set_led_status('green')
            success = True
        else:
            set_led_status('red')
            success = False
        
        return jsonify({
            'success': success,
            'identity': identity,
            'cw': cw,
            'rw': rw,
            'authResult': auth_result,
            'claimId': claim_id,
            'message': f'Authentication completed: {auth_result}'
        })
    except Exception as e:
        set_led_status('red')
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Full authentication failed'
        }), 500

@app.route('/api/led-status', methods=['POST'])
def set_led():
    """Manually control the LED"""
    try:
        data = request.get_json()
        status = data.get('status')
        
        if status not in ['green', 'red', 'yellow']:
            return jsonify({
                'success': False,
                'error': 'Status must be green, red, or yellow'
            }), 400
        
        set_led_status(status)
        return jsonify({
            'success': True,
            'message': f'LED set to {status}'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to set LED'
        }), 500

if __name__ == '__main__':
    print("Starting Raspberry Pi API Server...")
    print("Available endpoints:")
    print("  GET  /api/status")
    print("  GET  /api/get-identity")
    print("  POST /api/get-cw")
    print("  POST /api/get-rw")
    print("  POST /api/authenticate")
    print("  GET  /api/full-auth")
    print("  POST /api/led-status")
    
    app.run(host='0.0.0.0', port=5000, debug=True)