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

# Optimized retry settings
MAX_RETRIES = 5
OPERATION_TIMEOUT = 30.0

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

# ==================== DEVICE POOL ====================
class DeviceConfig:
    def __init__(self, device_id, serial_port):
        self.device_id = device_id
        self.serial_port = serial_port
        self.lock = threading.Lock()
        self.last_operation_time = 0
        self.consecutive_failures = 0
        self.total_operations = 0
        self.successful_operations = 0

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
            
            if not ports:
                logger.error("No serial devices found!")
                return False
            
            for idx, port in enumerate(ports):
                try:
                    # Quick test
                    ser = serial.Serial(port, 115200, timeout=0.5)
                    ser.close()
                    time.sleep(0.05)
                    
                    device = DeviceConfig(device_id=idx, serial_port=port)
                    self.devices.append(device)
                    logger.info(f"Device {idx} registered: {port}")
                except Exception as e:
                    logger.warning(f"Failed to register {port}: {e}")
        
        self.initialized = True
        logger.info(f"Device pool initialized with {len(self.devices)} device(s)")
        return len(self.devices) > 0
    
    def get_device(self):
        """Round-robin device selection"""
        if not self.devices:
            if not self.initialize():
                raise Exception("No devices available")
        
        with self._lock:
            device = self.devices[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.devices)
            return device

_device_pool = DevicePool()

# ==================== OPTIMIZED SERIAL COMMUNICATION ====================
@contextmanager
def safe_serial_connection(device):
    """Safe serial connection with proper timing"""
    ser = None
    try:
        # Enforce minimum interval
        elapsed = time.time() - device.last_operation_time
        min_interval = 0.25
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        
        # Open port
        ser = serial.Serial(
            port=device.serial_port,
            baudrate=115200,
            timeout=3.0,
            write_timeout=3.0,
            exclusive=True,
            inter_byte_timeout=0.2
        )
        
        # Device settling time (CRITICAL!)
        time.sleep(0.15)
        
        # Clear buffers thoroughly
        for _ in range(3):
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.03)
        
        yield ser
        
    finally:
        if ser and ser.is_open:
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                time.sleep(0.05)
                ser.close()
            except:
                pass
        
        device.last_operation_time = time.time()
        # Post-operation rest (prevents "device not ready" errors)
        time.sleep(0.2)

def read_response_smart(ser, expected_bytes=144, timeout=5.0):
    """Smart response reading with proper timeout handling"""
    response = b''
    start_time = time.time()
    last_data_time = start_time
    no_data_cycles = 0
    max_no_data_cycles = 15  # 0.3 seconds without data
    
    while time.time() - start_time < timeout:
        try:
            waiting = ser.in_waiting
            
            if waiting > 0:
                # Data available
                chunk = ser.read(waiting)
                if len(chunk) > 0:
                    response += chunk
                    last_data_time = time.time()
                    no_data_cycles = 0
                    
                    # Check if we have enough
                    if len(response) >= expected_bytes:
                        break
                else:
                    # THE PROBLEM: in_waiting > 0 but read returns nothing
                    no_data_cycles += 1
                    if no_data_cycles > 3:
                        logger.warning(f"Ghost data detected (waiting={waiting}, got=0)")
                        # Force a small delay and continue
                        time.sleep(0.1)
                        no_data_cycles = 0
            else:
                # No data waiting
                if len(response) > 0:
                    # We have some data, check if we should stop waiting
                    stall_time = time.time() - last_data_time
                    if stall_time > 0.5:
                        # No new data for 0.5s, probably complete
                        if len(response) >= expected_bytes * 0.7:  # At least 70% of expected
                            break
                
                no_data_cycles += 1
                if no_data_cycles > max_no_data_cycles:
                    # Too many cycles without data
                    if len(response) == 0:
                        raise Exception("Device not responding")
                    else:
                        # Have some data, use it
                        break
            
            time.sleep(0.02)
            
        except serial.SerialException as e:
            logger.error(f"Serial exception during read: {e}")
            if len(response) > 0:
                break  # Use what we have
            raise
    
    if len(response) == 0:
        raise Exception("No data received from device")
    
    return response

def parse_hex_response(response_bytes):
    """Parse hex response with robust error handling"""
    # Decode to string
    try:
        response_str = response_bytes.decode('utf-8', errors='ignore')
    except:
        response_str = response_bytes.decode('ascii', errors='ignore')
    
    # Extract only hex characters
    hex_chars = ''.join(c for c in response_str if c in '0123456789abcdefABCDEF')
    
    if len(hex_chars) < 10:
        raise Exception(f"Invalid response format (only {len(hex_chars)} hex chars)")
    
    # Convert to byte list
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
    """Optimized serial transfer with smart retry"""
    if not _device_pool.initialized:
        _device_pool.initialize()
    
    device = _device_pool.get_device()
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        with device.lock:
            try:
                logger.debug(f"Transfer attempt {attempt + 1}/{MAX_RETRIES}")
                
                with safe_serial_connection(device) as ser:
                    # Send command
                    cmd = ''.join('%02x' % e for e in l) + "\r"
                    bytes_written = ser.write(cmd.encode('utf-8'))
                    ser.flush()
                    
                    if bytes_written != len(cmd):
                        raise Exception(f"Incomplete write: {bytes_written}/{len(cmd)} bytes")
                    
                    # CRITICAL: Wait for device to process command
                    # This is the KEY to preventing "no data" errors
                    if l[0] == 0x01:  # Identity command
                        process_time = 0.25
                    elif l[0] == 0x03:  # Challenge-response
                        process_time = 0.30
                    else:
                        process_time = 0.25
                    
                    time.sleep(process_time)
                    
                    # Read response
                    response_bytes = read_response_smart(ser, expected_bytes=144, timeout=5.0)
                    
                    # Parse response
                    byte_list = parse_hex_response(response_bytes)
                    
                    # Success!
                    device.consecutive_failures = 0
                    device.successful_operations += 1
                    device.total_operations += 1
                    
                    logger.info(f"✓ Transfer successful: {len(byte_list)} bytes")
                    return byte_list
                    
            except Exception as e:
                last_error = e
                device.consecutive_failures += 1
                device.total_operations += 1
                
                logger.warning(f"✗ Attempt {attempt + 1} failed: {e}")
                
                # Exponential backoff
                if attempt < MAX_RETRIES - 1:
                    backoff = min(0.5 * (2 ** attempt), 3.0)  # Max 3 seconds
                    logger.info(f"Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)
                    
                    # Hard reset on consecutive failures
                    if device.consecutive_failures >= 3:
                        logger.warning(f"Device has {device.consecutive_failures} failures - power cycle recommended")
    
    # All retries failed
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
        raise Exception(f"Response too short: {len(l_r)} bytes (expected at least 37)")
    
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    return l_pcc, l_id

def disassemble_l_rw(l_r):
    if len(l_r) < API_I_RESP_START + API_I_RESP_LENGTH:
        raise Exception(f"Response too short: {len(l_r)} bytes (expected at least 87)")
    
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw = l_r[API_I_RESP_START : API_I_RESP_START + API_I_RESP_LENGTH]
    return l_pcc, l_id, l_rw

# ==================== DEVICE OPERATIONS ====================
def get_pccid():
    """Get PCCID from device"""
    logger.info("Getting PCCID...")
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    result = s_pcc + s_id
    
    logger.info(f"PCCID retrieved: {result}")
    return result

def do_rw_only(cw_l):
    """Get RW response from device"""
    logger.info("Getting RW...")
    l_r = do_transfer_l(assemble_cw_l(cw_l))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    
    s_rw = ''.join('%02x' % e for e in l_rw)
    logger.info(f"RW retrieved: {s_rw}")
    return s_rw

# ==================== CYBERROCK API FUNCTIONS ====================
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):
    logger.debug("Logging in to CyberRock IoT...")
    response = requests.post(cyberrock_iot_login,
        headers=cloudflaretokens,
        data={'username': iotusername, 'password': iotpassword},
        timeout=15)
    logindata = response.json()
    return logindata['accessToken'], logindata['iotId']

def get_cyberrock_cw(cloudflaretokens, accesstoken, PCCID, requestSignature):
    logger.debug("Requesting CW from CyberRock...")
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"requestSignedResponse": requestSignature, "PCCID": PCCID}
    response = requests.post(cyberrock_iot_requestcw,
        headers=data_auth, json=data_post, timeout=15)
    cwdata = response.json()
    return cwdata['CW'], cwdata['transactionId']

def do_submit_rw(cloudflaretokens, accesstoken, PCCID, CW, RW, transactionid, requestSignature):
    logger.debug("Submitting RW to CyberRock...")
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
    logger.debug("Retrieving auth result from CyberRock...")
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    params_post = {"transactionId": transactionid}
    data_post = {"requestSignedResponse": requestSignature}
    
    authenticationresult = 'NOT_READY'
    max_attempts = 40
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.3)
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
logger.info(f"SGA Module loaded - Environment: {environment}, Interface: {interface}")
logger.info(f"Retry policy: {MAX_RETRIES} attempts, {OPERATION_TIMEOUT}s timeout")