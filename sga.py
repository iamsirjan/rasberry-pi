import random, sys, time, requests
import serial
import RPi.GPIO as GPIO
from math import log, ceil
import threading
import glob
from contextlib import contextmanager

# ==================== CONFIGURATION ====================
environment = 'SANDBOX'
interface = 'USB'

# API Endpoints
if environment == 'SANDBOX':
    cyberrock_iot_login = 'https://iot-api.sandbox.sandgrain.io/api/auth/iotLogin'
    cyberrock_iot_requestcw = 'https://iot-api.sandbox.sandgrain.io/api/iot/requestCW'
    cyberrock_iot_replyrw = 'https://iot-api.sandbox.sandgrain.io/api/iot/replyRW'
    cyberrock_iot_checkstatus = 'https://iot-api.sandbox.sandgrain.io/api/iot/checkAuthStatus'
    cyberrock_iot_immediateauth = 'https://iot-api.sandbox.sandgrain.io/api/iot/immediateAuth'
    cyberrock_iot_requestRWtransactionid = 'https://iot-api.sandbox.sandgrain.io/api/iot/requestTransactionID'
    cyberrock_iot_requestRW = 'https://iot-api.sandbox.sandgrain.io/api/iot/requestRW'
    cyberrock_iot_requestRWstatus = 'https://iot-api.sandbox.sandgrain.io/api/iot/checkRequestRWStatus'
    cyberrock_tenant_login = 'https://tenant-api.sandbox.sandgrain.io/api/auth/tenantUserLogin'
    cyberrock_tenant_claimid = 'https://tenant-api.sandbox.sandgrain.io/api/tenantApi/claimId'

# Command definitions
l_command_ident = [0x01, 0x00, 0x00, 0x00]
l_command_bist = [0x80, 0x00, 0x00, 0x00]
l_command_cr = [0x03, 0x00, 0x08, 0x00]
l_command_cr_ek = [0x07, 0x00, 0x08, 0x00]

# Response indices
API_I_IDENT_PART1_START = 5
API_I_IDENT_PART1_LENGTH = 16
API_I_IDENT_PART2_START = 21
API_I_IDENT_PART2_LENGTH = 16
API_I_IDENT_START = 5
API_I_IDENT_LENGTH = 32
API_I_CHAL_START = 38
API_I_CHAL_LENGTH = 32
API_I_RESP_START = 71
API_I_RESP_LENGTH = 16
API_I_EK_START = 87
API_I_EK_LENGTH = 16
API_I_BIST = 71

# ==================== DEVICE POOL WITH RETRY LOGIC ====================
class DeviceConfig:
    def __init__(self, device_id, serial_port):
        self.device_id = device_id
        self.serial_port = serial_port
        self.lock = threading.Lock()
        self.last_operation_time = 0
        self.consecutive_failures = 0
        self.max_failures = 3

class DevicePool:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.devices = []
                    cls._instance.current_index = 0
                    cls._instance.initialized = False
        return cls._instance
    
    def initialize(self):
        if self.initialized:
            return True
        
        if interface == 'USB':
            ports = sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))
            for idx, port in enumerate(ports):
                try:
                    ser = serial.Serial(port, 115200, timeout=0.5)
                    ser.close()
                    time.sleep(0.1)
                    device = DeviceConfig(device_id=idx, serial_port=port)
                    self.devices.append(device)
                    print(f"[SGA] Device {idx}: {port}")
                except Exception as e:
                    print(f"[SGA] Failed to open {port}: {e}")
        
        self.initialized = True
        print(f"[SGA] Initialized {len(self.devices)} device(s)")
        return len(self.devices) > 0
    
    def get_device(self):
        if not self.devices:
            raise Exception("No devices available")
        
        with self._lock:
            # Find device with lowest failure count
            best_device = min(self.devices, key=lambda d: d.consecutive_failures)
            return best_device
    
    def mark_success(self, device):
        device.consecutive_failures = 0
    
    def mark_failure(self, device):
        device.consecutive_failures += 1

_device_pool = DevicePool()

# ==================== ROBUST SERIAL COMMUNICATION ====================
@contextmanager
def safe_serial(device, operation_name="unknown"):
    """Context manager for safe serial communication with recovery"""
    ser = None
    try:
        # Enforce minimum delay between operations
        elapsed = time.time() - device.last_operation_time
        min_delay = 0.3  # Increased from 0.15
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)
        
        # Open serial with increased timeout
        ser = serial.Serial(
            port=device.serial_port,
            baudrate=115200,
            timeout=3.0,  # Increased from 2.0
            write_timeout=4.0,
            exclusive=True
        )
        
        # Wait for port to stabilize
        time.sleep(0.15)  # Increased from 0.08
        
        # Clear buffers multiple times for reliability
        for _ in range(2):
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.05)
        
        yield ser
        
        _device_pool.mark_success(device)
        
    except serial.SerialException as e:
        _device_pool.mark_failure(device)
        print(f"[SGA] Serial error on {device.serial_port} during {operation_name}: {e}")
        raise Exception(f"Serial communication error: {str(e)}")
    except Exception as e:
        _device_pool.mark_failure(device)
        print(f"[SGA] Unexpected error on {device.serial_port} during {operation_name}: {e}")
        raise
    finally:
        if ser and ser.is_open:
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.close()
            except:
                pass
        device.last_operation_time = time.time()
        time.sleep(0.15)  # Increased post-operation delay

def do_ser_transfer_l(l, max_retries=5):
    """Optimized serial transfer with automatic retry"""
    
    if not _device_pool.initialized:
        _device_pool.initialize()
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            device = _device_pool.get_device()
            
            # Skip device if too many consecutive failures
            if device.consecutive_failures >= device.max_failures:
                print(f"[SGA] Skipping device {device.device_id} due to failures")
                continue
            
            with device.lock:
                with safe_serial(device, f"transfer_attempt_{attempt+1}") as ser:
                    # Prepare command
                    l_s = ''.join('%02x' % e for e in l) + "\r"
                    
                    # Send command
                    ser.write(l_s.encode('utf-8'))
                    ser.flush()
                    
                    # CRITICAL: Wait for device processing
                    # Different commands may need different delays
                    if l[0] == 0x01:  # Identity command
                        time.sleep(0.15)
                    elif l[0] == 0x03:  # Challenge-response
                        time.sleep(0.2)
                    else:
                        time.sleep(0.15)
                    
                    # Read response with robust timeout handling
                    resp_s = b''
                    start_time = time.time()
                    last_data_time = start_time
                    max_wait = 3.0  # Maximum wait time
                    
                    while time.time() - start_time < max_wait:
                        if ser.in_waiting > 0:
                            chunk = ser.read(ser.in_waiting)
                            if len(chunk) > 0:
                                resp_s += chunk
                                last_data_time = time.time()
                                
                                # Check if we have complete response (144 chars = 72 bytes hex)
                                if len(resp_s) >= 144:
                                    break
                        else:
                            # If we have data and no new data for 0.4s, consider complete
                            if len(resp_s) > 0 and (time.time() - last_data_time) > 0.4:
                                break
                            time.sleep(0.01)
                    
                    # Validate response
                    if len(resp_s) == 0:
                        raise Exception("No data received from device")
                    
                    # Clean and parse response
                    resp_clean = resp_s.strip()
                    
                    # Remove any non-hex characters
                    resp_hex = ''.join(c for c in resp_clean.decode('utf-8', errors='ignore') 
                                      if c in '0123456789abcdefABCDEF')
                    
                    if len(resp_hex) < 10:
                        raise Exception(f"Response too short: {len(resp_hex)} chars")
                    
                    # Parse hex string to byte list
                    try:
                        l_r = [int(resp_hex[i:i+2], 16) for i in range(0, len(resp_hex), 2)]
                    except ValueError as e:
                        raise Exception(f"Invalid hex response: {e}")
                    
                    if len(l_r) < 5:
                        raise Exception(f"Parsed response too short: {len(l_r)} bytes")
                    
                    print(f"[SGA] Success on attempt {attempt+1}: {len(l_r)} bytes")
                    return l_r
                    
        except Exception as e:
            last_error = e
            print(f"[SGA] Attempt {attempt+1}/{max_retries} failed: {e}")
            
            if attempt < max_retries - 1:
                # Exponential backoff
                wait_time = 0.3 * (attempt + 1)
                print(f"[SGA] Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[SGA] All {max_retries} attempts failed")
    
    # All retries exhausted
    raise Exception(f"Failed after {max_retries} attempts. Last error: {last_error}")

# Set the transfer function
do_transfer_l = do_ser_transfer_l

# ==================== HELPER FUNCTIONS ====================
def list_invert(l):
    return [(~e) & 0xFF for e in l]

def intToList(number):
    L1 = log(number, 256)
    L2 = ceil(L1)
    if L1 == L2:
        L2 += 1
    return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]

# ==================== COMMAND ASSEMBLY ====================
def assemble_bist_l():
    return l_command_bist + [0]*68

def assemble_id_l():
    return l_command_ident + [0] + [0]*32

def assemble_cw_l(l_challenge):
    return l_command_cr + [0] + l_challenge + [0] + [0]*49

def assemble_ek_l(l_challenge):
    return l_command_cr_ek + [0] + l_challenge + [0] + [0]*65

# ==================== RESPONSE DISASSEMBLY ====================
def disassemble_l_id(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    return l_pcc, l_id

def disassemble_l_rw(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw = l_r[API_I_RESP_START : API_I_RESP_START + API_I_RESP_LENGTH]
    return l_pcc, l_id, l_rw

# ==================== DEVICE OPERATIONS ====================
def get_pccid():
    """Get PCCID from device with retry logic"""
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    return s_pcc + s_id

def do_rw_only(cw_l):
    """Get RW from device given CW with retry logic"""
    l_r = do_transfer_l(assemble_cw_l(cw_l))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    s_rw = ''.join('%02x' % e for e in l_rw)
    return s_rw

# ==================== CYBERROCK API FUNCTIONS ====================
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):
    response = requests.post(cyberrock_iot_login,
        headers=cloudflaretokens,
        data={'username': iotusername, 'password': iotpassword},
        timeout=15)  # Increased timeout
    logindata = response.json()
    return logindata['accessToken'], logindata['iotId']

def get_cyberrock_cw(cloudflaretokens, accesstoken, PCCID, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"requestSignedResponse": requestSignature, "PCCID": PCCID}
    response = requests.post(cyberrock_iot_requestcw,
        headers=data_auth, json=data_post, timeout=15)
    cwdata = response.json()
    return cwdata['CW'], cwdata['transactionId']

def do_submit_rw(cloudflaretokens, accesstoken, PCCID, CW, RW, transactionid, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {
        "requestSignedResponse": requestSignature,
        "PCCID": PCCID,
        "CW": CW,
        "RW": RW,
        "transactionId": transactionid
    }
    response = requests.post(cyberrock_iot_replyrw,
        headers=data_auth, json=data_post, timeout=15)
    return response.json()['transactionId']

def do_retrieve_result(cloudflaretokens, accesstoken, transactionid, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    params_post = {"transactionId": transactionid}
    data_post = {"requestSignedResponse": requestSignature}
    
    authenticationresult = 'NOT_READY'
    max_attempts = 30
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.3)  # Increased from 0.2
        response = requests.get(cyberrock_iot_checkstatus,
            headers=data_auth, params=params_post, json=data_post, timeout=15)
        responsedata = response.json()
        authenticationresult = responsedata['status']
        attempt += 1
    
    claimid = responsedata.get('claimId', '') if authenticationresult == 'CLAIM_ID' else ''
    return authenticationresult, claimid

# ==================== INITIALIZATION ====================
def gpio_setup():
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
    except:
        pass

gpio_setup()
print(f"[SGA] Module loaded - Environment: {environment}, Interface: {interface}")
print(f"[SGA] Retry logic enabled with 3 attempts per operation")
