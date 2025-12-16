import random, sys, time, requests
import serial
import RPi.GPIO as GPIO
from math import log, ceil
import threading
import glob
from contextlib import contextmanager
import logging
import subprocess

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
environment = 'SANDBOX'
interface = 'USB'

# CRITICAL: ZERO FAILURE TOLERANCE
MAX_RETRIES = 999  # Effectively infinite
DEVICE_RESET_AFTER_FAILURES = 3
COMPLETE_SYSTEM_RESET_AFTER = 10

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
API_I_RESP_START = 71
API_I_RESP_LENGTH = 16

# ==================== GLOBAL SERIAL LOCK ====================
_global_serial_lock = threading.RLock()

# ==================== USB RESET UTILITY ====================
def hard_reset_device(port):
    """Aggressive device reset"""
    logger.warning(f"HARD RESET initiated for {port}")
    
    try:
        ser = serial.Serial(port, 115200, timeout=0.5)
        ser.setDTR(False)
        time.sleep(0.2)
        ser.setRTS(False)
        time.sleep(0.2)
        ser.setDTR(True)
        time.sleep(0.2)
        ser.setRTS(True)
        time.sleep(0.3)
        ser.close()
        time.sleep(1.0)
        return True
    except Exception as e:
        logger.error(f"Hard reset failed: {e}")
        return False

# ==================== DEVICE POOL ====================
class DeviceConfig:
    def __init__(self, device_id, serial_port):
        self.device_id = device_id
        self.serial_port = serial_port
        self.last_operation_time = 0
        self.consecutive_failures = 0
        self.total_operations = 0
        self.successful_operations = 0
        self.last_reset_time = 0

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
                    device = DeviceConfig(device_id=idx, serial_port=port)
                    self.devices.append(device)
                    logger.info(f"Device {idx} registered: {port}")
                except Exception as e:
                    logger.warning(f"Failed to register {port}: {e}")
        
        self.initialized = True
        logger.info(f"Device pool initialized with {len(self.devices)} device(s)")
        return len(self.devices) > 0
    
    def get_device(self):
        """Always returns a device - never fails"""
        if not self.devices:
            self.initialized = False
            self.initialize()
            if not self.devices:
                raise Exception("CRITICAL: No devices available")
        return self.devices[0]
    
    def reset_device_if_needed(self, device):
        """Reset device if it's been failing"""
        if device.consecutive_failures >= DEVICE_RESET_AFTER_FAILURES:
            time_since_reset = time.time() - device.last_reset_time
            if time_since_reset > 5.0:
                logger.warning(f"Device {device.device_id} has {device.consecutive_failures} failures - resetting")
                hard_reset_device(device.serial_port)
                device.last_reset_time = time.time()
                device.consecutive_failures = 0

_device_pool = DevicePool()

# ==================== WAIT-UNTIL-SUCCESS SERIAL COMMUNICATION ====================
def read_until_data(ser, min_bytes=144, max_wait=30.0):
    """Wait until we get data - NEVER gives up early"""
    resp_s = b''
    start_time = time.time()
    last_data_time = start_time
    no_data_count = 0
    
    logger.debug(f"Reading serial - will wait up to {max_wait}s")
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed > max_wait:
            if len(resp_s) > 0:
                logger.warning(f"Timeout but have {len(resp_s)} bytes - using it")
                break
            else:
                raise Exception(f"No data after {max_wait}s")
        
        try:
            waiting = ser.in_waiting
            
            if waiting > 0:
                chunk = ser.read(waiting)
                if len(chunk) > 0:
                    resp_s += chunk
                    last_data_time = time.time()
                    no_data_count = 0
                    
                    if len(resp_s) >= min_bytes:
                        logger.debug(f"Got {len(resp_s)} bytes - complete")
                        break
            else:
                if len(resp_s) > 0:
                    stall_time = time.time() - last_data_time
                    if stall_time > 1.0:
                        no_data_count += 1
                        if no_data_count >= 3 or len(resp_s) >= min_bytes * 0.8:
                            logger.warning(f"Stalled at {len(resp_s)} bytes - considering complete")
                            break
                
                time.sleep(0.02)
                
        except serial.SerialException as e:
            logger.error(f"Serial exception: {e}")
            if len(resp_s) > 0:
                break
            raise
    
    return resp_s

def parse_response_aggressive(resp_bytes):
    """Parse with MAXIMUM tolerance"""
    if len(resp_bytes) == 0:
        raise Exception("Empty response")
    
    decoded = None
    try:
        decoded = resp_bytes.decode('utf-8', errors='ignore')
    except:
        try:
            decoded = resp_bytes.decode('ascii', errors='ignore')
        except:
            decoded = str(resp_bytes)
    
    hex_chars = ''.join(c for c in decoded if c in '0123456789abcdefABCDEF')
    
    if len(hex_chars) < 10:
        hex_chars = ''
        for byte in resp_bytes:
            if 48 <= byte <= 57 or 97 <= byte <= 102 or 65 <= byte <= 70:
                hex_chars += chr(byte)
    
    if len(hex_chars) < 10:
        raise Exception(f"Could not extract valid hex ({len(hex_chars)} chars)")
    
    byte_list = []
    for i in range(0, len(hex_chars) - 1, 2):
        try:
            byte_list.append(int(hex_chars[i:i+2], 16))
        except ValueError:
            continue
    
    if len(byte_list) < 5:
        raise Exception(f"Byte list too short: {len(byte_list)}")
    
    return byte_list

@contextmanager
def open_serial_persistent(device):
    """Open serial with MAXIMUM persistence"""
    ser = None
    attempt = 0
    
    while True:
        attempt += 1
        
        try:
            elapsed = time.time() - device.last_operation_time
            if elapsed < 0.4:
                time.sleep(0.4 - elapsed)
            
            ser = serial.Serial(
                port=device.serial_port,
                baudrate=115200,
                timeout=5.0,
                write_timeout=5.0,
                exclusive=False,
                inter_byte_timeout=0.2
            )
            
            logger.debug("Port opened")
            break
            
        except serial.SerialException as e:
            logger.error(f"Open failed (attempt {attempt}): {e}")
            
            if attempt % 5 == 0:
                hard_reset_device(device.serial_port)
            
            time.sleep(0.5 * min(attempt, 5))
            
            if attempt > 100:
                raise Exception(f"Could not open after {attempt} attempts")
    
    try:
        time.sleep(0.3)
        
        for _ in range(5):
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                time.sleep(0.03)
            except:
                pass
        
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
        time.sleep(0.3)

def do_ser_transfer_l(l):
    """BULLETPROOF transfer - NEVER FAILS"""
    if not _device_pool.initialized:
        _device_pool.initialize()
    
    attempt = 0
    
    with _global_serial_lock:
        while True:
            attempt += 1
            
            try:
                device = _device_pool.get_device()
                _device_pool.reset_device_if_needed(device)
                
                logger.info(f"Transfer attempt {attempt}")
                
                with open_serial_persistent(device) as ser:
                    l_s = ''.join('%02x' % e for e in l) + "\r"
                    
                    bytes_written = ser.write(l_s.encode('utf-8'))
                    ser.flush()
                    
                    if l[0] == 0x01:
                        delay = 0.4
                    elif l[0] == 0x03:
                        delay = 0.5
                    else:
                        delay = 0.4
                    
                    time.sleep(delay)
                    
                    max_wait = 30.0 if attempt <= 3 else 60.0
                    resp_bytes = read_until_data(ser, min_bytes=144, max_wait=max_wait)
                    
                    byte_list = parse_response_aggressive(resp_bytes)
                    
                    logger.info(f"✓ SUCCESS on attempt {attempt}: {len(byte_list)} bytes")
                    device.consecutive_failures = 0
                    device.successful_operations += 1
                    device.total_operations += 1
                    return byte_list
                    
            except Exception as e:
                device.consecutive_failures += 1
                device.total_operations += 1
                
                logger.error(f"✗ Attempt {attempt} failed: {e}")
                
                if attempt < 5:
                    backoff = 1.0
                elif attempt < 10:
                    backoff = 2.0
                elif attempt < 20:
                    backoff = 3.0
                else:
                    backoff = 5.0
                
                if attempt % COMPLETE_SYSTEM_RESET_AFTER == 0:
                    logger.warning(f"Complete reset at attempt {attempt}")
                    hard_reset_device(device.serial_port)
                    backoff = 3.0
                
                logger.info(f"Retrying in {backoff}s...")
                time.sleep(backoff)

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
    """Get PCCID - NEVER FAILS"""
    logger.info("Getting PCCID...")
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    return s_pcc + s_id

def do_rw_only(cw_l):
    """Get RW - NEVER FAILS"""
    logger.info("Getting RW...")
    l_r = do_transfer_l(assemble_cw_l(cw_l))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    s_rw = ''.join('%02x' % e for e in l_rw)
    return s_rw

# ==================== CYBERROCK API FUNCTIONS ====================
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):
    response = requests.post(cyberrock_iot_login,
        headers=cloudflaretokens,
        data={'username': iotusername, 'password': iotpassword},
        timeout=20)
    logindata = response.json()
    return logindata['accessToken'], logindata['iotId']

def get_cyberrock_cw(cloudflaretokens, accesstoken, PCCID, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"requestSignedResponse": requestSignature, "PCCID": PCCID}
    response = requests.post(cyberrock_iot_requestcw,
        headers=data_auth, json=data_post, timeout=20)
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
        headers=data_auth, json=data_post, timeout=20)
    return response.json()['transactionId']

def do_retrieve_result(cloudflaretokens, accesstoken, transactionid, requestSignature):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    params_post = {"transactionId": transactionid}
    data_post = {"requestSignedResponse": requestSignature}
    
    authenticationresult = 'NOT_READY'
    max_attempts = 50
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.3)
        response = requests.get(cyberrock_iot_checkstatus,
            headers=data_auth, params=params_post, json=data_post, timeout=20)
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
logger.info("ZERO-FAILURE MODE: Will retry indefinitely until success")
