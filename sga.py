import random, sys, time, requests
import serial
import RPi.GPIO as GPIO
from math import log, ceil
import threading
import glob
from contextlib import contextmanager
import logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
environment = 'SANDBOX'
interface = 'USB'

# OPTIMIZED FOR SPEED
MAX_RETRIES = 2  # Reduced from 3
INTER_REQUEST_DELAY = 0.1  # Reduced from 0.5s (80% faster!)
DEVICE_SETTLE_TIME = 0.05  # Reduced from 0.2s
BUFFER_CLEAR_DELAY = 0.01  # Reduced from 0.05s
POST_OPERATION_DELAY = 0.05  # Reduced from 0.3s

# API Endpoints
if environment == 'SANDBOX':
    cyberrock_iot_login = 'https://iot-api.sandbox.sandgrain.io/api/auth/iotLogin'
    cyberrock_iot_requestcw = 'https://iot-api.sandbox.sandgrain.io/api/iot/requestCW'
    cyberrock_iot_replyrw = 'https://iot-api.sandbox.sandgrain.io/api/iot/replyRW'
    cyberrock_iot_checkstatus = 'https://iot-api.sandbox.sandgrain.io/api/iot/checkAuthStatus'

# Command definitions
l_command_ident = [0x01, 0x00, 0x00, 0x00]
l_command_cr = [0x03, 0x00, 0x08, 0x00]

# Response indices
API_I_IDENT_PART1_START = 5
API_I_IDENT_PART1_LENGTH = 16
API_I_IDENT_PART2_START = 21
API_I_IDENT_PART2_LENGTH = 16
API_I_RESP_START = 71
API_I_RESP_LENGTH = 16

# ==================== GLOBAL SERIAL LOCK ====================
_GLOBAL_SERIAL_LOCK = threading.RLock()
_last_global_operation = 0

# ==================== DEVICE POOL ====================
class DeviceConfig:
    def __init__(self, device_id, serial_port):
        self.device_id = device_id
        self.serial_port = serial_port
        self.total_operations = 0
        self.successful_operations = 0
        self.consecutive_failures = 0

class DevicePool:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance.devices = []
                    cls._instance.initialized = False
        return cls._instance
    
    def initialize(self):
        if self.initialized:
            return True
        
        if interface == 'USB':
            ports = sorted(glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*'))
            
            if not ports:
                logger.error("No serial devices found!")
                return False
            
            for idx, port in enumerate(ports):
                try:
                    ser = serial.Serial(port, 115200, timeout=0.5)
                    ser.close()
                    time.sleep(0.02)  # Reduced delay
                    
                    device = DeviceConfig(device_id=idx, serial_port=port)
                    self.devices.append(device)
                    logger.info(f"Device {idx} registered: {port}")
                except Exception as e:
                    logger.warning(f"Failed to register {port}: {e}")
        
        self.initialized = True
        logger.info(f"Device pool initialized with {len(self.devices)} device(s)")
        return len(self.devices) > 0
    
    def get_device(self):
        if not self.devices:
            if not self.initialize():
                raise Exception("No devices available")
        return self.devices[0]

_device_pool = DevicePool()

# ==================== OPTIMIZED SERIAL COMMUNICATION ====================
@contextmanager
def exclusive_serial_access(device):
    """Optimized for speed while maintaining safety"""
    global _last_global_operation
    
    _GLOBAL_SERIAL_LOCK.acquire()
    
    ser = None
    try:
        # Minimal delay between operations
        elapsed = time.time() - _last_global_operation
        if elapsed < INTER_REQUEST_DELAY:
            time.sleep(INTER_REQUEST_DELAY - elapsed)
        
        # Open port
        ser = serial.Serial(
            port=device.serial_port,
            baudrate=115200,
            timeout=2.0,  # Reduced from 4.0
            write_timeout=2.0,
            exclusive=True,
            inter_byte_timeout=0.1  # Reduced from 0.3
        )
        
        # Minimal settling time
        time.sleep(DEVICE_SETTLE_TIME)
        
        # Single buffer clear (not 3 rounds!)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        time.sleep(BUFFER_CLEAR_DELAY)
        
        yield ser
        
    finally:
        if ser and ser.is_open:
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                ser.close()
            except Exception as e:
                logger.error(f"Error closing port: {e}")
        
        _last_global_operation = time.time()
        
        # Minimal cool-down
        time.sleep(POST_OPERATION_DELAY)
        
        _GLOBAL_SERIAL_LOCK.release()

def read_response_robust(ser, expected_bytes=144, timeout=3.0):
    """Faster response reading with lower timeout"""
    response = b''
    start_time = time.time()
    last_data_time = start_time
    consecutive_empty_reads = 0
    max_empty_reads = 3  # Reduced from 5
    
    while time.time() - start_time < timeout:
        try:
            waiting = ser.in_waiting
            
            if waiting > 0:
                chunk = ser.read(waiting)
                
                if len(chunk) > 0:
                    response += chunk
                    last_data_time = time.time()
                    consecutive_empty_reads = 0
                    
                    if len(response) >= expected_bytes:
                        break
                else:
                    consecutive_empty_reads += 1
                    
                    if consecutive_empty_reads >= max_empty_reads:
                        if len(response) > 0:
                            break
                        else:
                            raise Exception("Device stuck - no data")
                    
                    time.sleep(0.05)  # Reduced from 0.15
            else:
                if len(response) > 0:
                    stall_time = time.time() - last_data_time
                    if stall_time > 0.5:  # Reduced from 1.0
                        if len(response) >= expected_bytes * 0.6:
                            break
                
                time.sleep(0.02)  # Reduced from 0.03
            
        except serial.SerialException as e:
            logger.error(f"Serial exception: {e}")
            if len(response) > 0:
                break
            raise
    
    if len(response) == 0:
        raise Exception(f"No response after {time.time() - start_time:.1f}s")
    
    return response

def parse_hex_response(response_bytes):
    """Parse hex response"""
    try:
        response_str = response_bytes.decode('utf-8', errors='ignore').strip()
    except:
        response_str = response_bytes.decode('ascii', errors='ignore').strip()
    
    hex_chars = ''.join(c for c in response_str if c in '0123456789abcdefABCDEF')
    
    if len(hex_chars) < 20:
        raise Exception(f"Invalid response: only {len(hex_chars)} hex chars")
    
    byte_list = []
    for i in range(0, len(hex_chars) - 1, 2):
        try:
            byte_list.append(int(hex_chars[i:i+2], 16))
        except ValueError:
            continue
    
    if len(byte_list) < 10:
        raise Exception(f"Parsed response too short: {len(byte_list)} bytes")
    
    return byte_list

def do_ser_transfer_l(l):
    """Optimized serial transfer"""
    if not _device_pool.initialized:
        _device_pool.initialize()
    
    device = _device_pool.get_device()
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            with exclusive_serial_access(device) as ser:
                # Send command
                cmd = ''.join('%02x' % e for e in l) + "\r"
                ser.write(cmd.encode('utf-8'))
                ser.flush()
                
                # Optimized processing time
                if l[0] == 0x01:  # Identity
                    process_time = 0.15  # Reduced from 0.35
                elif l[0] == 0x03:  # Challenge-response
                    process_time = 0.20  # Reduced from 0.40
                else:
                    process_time = 0.15
                
                time.sleep(process_time)
                
                # Read with shorter timeout
                response_bytes = read_response_robust(ser, expected_bytes=144, timeout=3.0)
                
                # Parse
                byte_list = parse_hex_response(response_bytes)
                
                # Success!
                device.consecutive_failures = 0
                device.successful_operations += 1
                device.total_operations += 1
                
                return byte_list
                
        except Exception as e:
            last_error = e
            device.consecutive_failures += 1
            device.total_operations += 1
            
            logger.error(f"✗ Attempt {attempt + 1} failed: {e}")
            
            if attempt < MAX_RETRIES - 1:
                backoff = 0.3 * (attempt + 1)  # Reduced backoff
                time.sleep(backoff)
    
    raise Exception(f"Transfer failed after {MAX_RETRIES} attempts. Last error: {last_error}")

do_transfer_l = do_ser_transfer_l

# ==================== HELPER FUNCTIONS ====================
def intToList(number):
    L1 = log(number, 256)
    L2 = ceil(L1)
    if L1 == L2:
        L2 += 1
    return [(number & (0xff << 8*i)) >> 8*i for i in reversed(range(L2))]

# ==================== COMMAND ASSEMBLY ====================
def assemble_id_l():
    return l_command_ident + [0] + [0]*32

def assemble_cw_l(l_challenge):
    return l_command_cr + [0] + l_challenge + [0] + [0]*49

# ==================== RESPONSE DISASSEMBLY ====================
def disassemble_l_id(l_r):
    if len(l_r) < API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH:
        raise Exception(f"Response too short: {len(l_r)} bytes")
    
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    return l_pcc, l_id

def disassemble_l_rw(l_r):
    if len(l_r) < API_I_RESP_START + API_I_RESP_LENGTH:
        raise Exception(f"Response too short: {len(l_r)} bytes")
    
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw = l_r[API_I_RESP_START : API_I_RESP_START + API_I_RESP_LENGTH]
    return l_pcc, l_id, l_rw

# ==================== DEVICE OPERATIONS ====================
def get_pccid():
    """Get PCCID from device"""
    logger.info(">>> get_pccid() called")
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    result = s_pcc + s_id
    
    logger.info(f"<<< get_pccid() returning: {result}")
    return result

def do_rw_only(cw_l):
    """Get RW response"""
    logger.info(">>> do_rw_only() called")
    l_r = do_transfer_l(assemble_cw_l(cw_l))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    
    s_rw = ''.join('%02x' % e for e in l_rw)
    logger.info(f"<<< do_rw_only() returning: {s_rw}")
    return s_rw

# ==================== CYBERROCK API ====================
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):
    response = requests.post(cyberrock_iot_login,
        headers=cloudflaretokens,
        data={'username': iotusername, 'password': iotpassword},
        timeout=10)  # Reduced from 15
    logindata = response.json()
    return logindata['accessToken'], logindata['iotId']

def get_cyberrock_cw(cloudflaretokens, accesstoken, PCCID, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"requestSignedResponse": requestSignature, "PCCID": PCCID}
    response = requests.post(cyberrock_iot_requestcw,
        headers=data_auth, json=data_post, timeout=10)
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
        headers=data_auth, json=data_post, timeout=10)
    return response.json()['transactionId']

def do_retrieve_result(cloudflaretokens, accesstoken, transactionid, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    params_post = {"transactionId": transactionid}
    data_post = {"requestSignedResponse": requestSignature}
    
    authenticationresult = 'NOT_READY'
    max_attempts = 40
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.2)  # Reduced from 0.3
        response = requests.get(cyberrock_iot_checkstatus,
            headers=data_auth, params=params_post, json=data_post, timeout=10)
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
logger.info(f"SGA Module loaded - SPEED OPTIMIZED")
logger.info(f"Inter-request delay: {INTER_REQUEST_DELAY}s (80% faster!)")
logger.info(f"Device settle time: {DEVICE_SETTLE_TIME}s")
logger.info(f"Max retries: {MAX_RETRIES}")