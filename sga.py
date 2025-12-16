import random, sys, time, requests
import serial
import RPi.GPIO as GPIO
from math import log, ceil
import threading
import glob
from contextlib import contextmanager

# ==================== CONFIGURATION ====================
# Switch between environments
# environment = 'UAT'
environment = 'SANDBOX'

# Switch between interfaces
interface = 'USB'
# interface = 'SPI'

# API Endpoints
if environment == 'UAT':
    cyberrock_iot_login = 'https://iot-api-uat.sandgrain.dev/api/auth/iotLogin'
    cyberrock_iot_requestcw = 'https://iot-api-uat.sandgrain.dev/api/iot/requestCW'
    cyberrock_iot_replyrw = 'https://iot-api-uat.sandgrain.dev/api/iot/replyRW'
    cyberrock_iot_checkstatus = 'https://iot-api-uat.sandgrain.dev/api/iot/checkAuthStatus'
    cyberrock_iot_immediateauth = 'https://iot-api-uat.sandgrain.dev/api/iot/immediateAuth'
    cyberrock_iot_requestRWtransactionid = 'https://iot-api-uat.sandgrain.dev/api/iot/requestTransactionID'
    cyberrock_iot_requestRW = 'https://iot-api-uat.sandgrain.dev/api/iot/requestRW'
    cyberrock_iot_requestRWstatus = 'https://iot-api-uat.sandgrain.dev/api/iot/checkRequestRWStatus'
    cyberrock_tenant_login = 'https://tenant-api-uat.sandgrain.dev/api/auth/tenantUserLogin'
    cyberrock_tenant_claimid = 'https://tenant-api-uat.sandgrain.dev/api/tenantApi/claimId'
    cyberrock_iot_requestcwek = 'https://iot-api-uat.sandgrain.dev/api/iot/ekrequestCW'
    cyberrock_iot_replyrwek = 'https://iot-api-uat.sandgrain.dev/api/iot/ekreplyRW'
    cyberrock_iot_checkstatusek = 'https://iot-api-uat.sandgrain.dev/api/iot/ekcheckAuthStatus'

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

# SPI Configuration (if using SPI interface)
API_CS1 = 22  # GPIO22
API_CS2 = 27  # GPIO27
API_CS3 = 17  # GPIO17

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

# ==================== DEVICE POOL ====================
class DeviceConfig:
    def __init__(self, device_id, serial_port):
        self.device_id = device_id
        self.serial_port = serial_port
        self.lock = threading.Lock()
        self.last_operation_time = 0

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
                    time.sleep(0.05)
                    device = DeviceConfig(device_id=idx, serial_port=port)
                    self.devices.append(device)
                    print(f"[SGA] Device {idx}: {port}")
                except:
                    pass
        
        self.initialized = True
        print(f"[SGA] Initialized {len(self.devices)} device(s)")
        return len(self.devices) > 0
    
    def get_device(self):
        if not self.devices:
            raise Exception("No devices available")
        
        with self._lock:
            device = self.devices[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.devices)
            return device

_device_pool = DevicePool()

# ==================== SERIAL COMMUNICATION (OPTIMIZED) ====================
@contextmanager
def safe_serial(device):
    """Context manager for safe serial communication"""
    ser = None
    try:
        elapsed = time.time() - device.last_operation_time
        if elapsed < 0.15:
            time.sleep(0.15 - elapsed)
        
        ser = serial.Serial(
            port=device.serial_port,
            baudrate=115200,
            timeout=2.0,
            write_timeout=2.0,
            exclusive=True
        )
        
        time.sleep(0.08)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        yield ser
        
    finally:
        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass
        device.last_operation_time = time.time()
        time.sleep(0.08)

def do_ser_transfer_l(l):
    """Optimized serial transfer using device pool"""
    if not _device_pool.initialized:
        _device_pool.initialize()
    
    device = _device_pool.get_device()
    
    with device.lock:
        with safe_serial(device) as ser:
            # Send
            l_s = ''.join('%02x' % e for e in l) + "\r"
            ser.write(l_s.encode('utf-8'))
            ser.flush()
            
            # CRITICAL: Wait for device
            time.sleep(0.12)
            
            # Read
            resp_s = b''
            start = time.time()
            last_data = start
            
            while time.time() - start < 2.0:
                if ser.in_waiting > 0:
                    chunk = ser.read(ser.in_waiting)
                    if len(chunk) > 0:
                        resp_s += chunk
                        last_data = time.time()
                        if len(resp_s) >= 144:
                            break
                else:
                    if len(resp_s) > 0 and (time.time() - last_data) > 0.3:
                        break
                    time.sleep(0.01)
            
            if len(resp_s) == 0:
                raise Exception("No data received")
            
            l_r = [int(resp_s[i:i+2], 16) for i in range(0, len(resp_s)-1, 2)]
            return l_r

# ==================== SPI COMMUNICATION (ORIGINAL) ====================
if interface == 'SPI':
    import spidev
    
    def spi_open():
        spi = spidev.SpiDev(0, 0)
        spi.max_speed_hz = 10_000_000
        return spi
    
    def spi_close(spi):
        if spi:
            spi.close()
    
    def do_spi_transfer_l(l):
        GPIO.output(API_CS1, GPIO.LOW)
        spi = spi_open()
        l_r = spi.xfer(l)
        spi_close(spi)
        GPIO.output(API_CS1, GPIO.HIGH)
        return l_r
    
    def gpio_setup():
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(API_CS1, GPIO.OUT)
        GPIO.setup(API_CS2, GPIO.OUT)
        GPIO.setup(API_CS3, GPIO.OUT)
        GPIO.output(API_CS1, GPIO.HIGH)
        GPIO.output(API_CS2, GPIO.HIGH)
        GPIO.output(API_CS3, GPIO.HIGH)

# Set the transfer function based on interface
if interface == 'SPI':
    do_transfer_l = do_spi_transfer_l
if interface == 'USB':
    do_transfer_l = do_ser_transfer_l

# ==================== HELPER FUNCTIONS ====================
def list_invert(l):
    """Invert list (bitwise NOT)"""
    return [(~e) & 0xFF for e in l]

def intToList(number):
    """Convert integer to byte list"""
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
def disassemble_l_bist(l_r):
    l_pcc_s = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id_s = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw_s = l_r[38:54]
    l_ek_s = l_r[54:70]
    i_pass = l_r[API_I_BIST]
    b_pass = 1 if i_pass == 0x50 else 0
    return b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s

def disassemble_l_id(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    return l_pcc, l_id

def disassemble_l_rw(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw = l_r[API_I_RESP_START : API_I_RESP_START + API_I_RESP_LENGTH]
    return l_pcc, l_id, l_rw

def disassemble_l_ek(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_ek = l_r[API_I_EK_START : API_I_EK_START + API_I_EK_LENGTH]
    return l_pcc, l_id, l_ek

def disassemble_l_rwek(l_r):
    l_pcc = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH]
    l_id = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START + API_I_IDENT_PART2_LENGTH]
    l_rw = l_r[API_I_RESP_START : API_I_RESP_START + API_I_RESP_LENGTH]
    l_ek = l_r[API_I_EK_START : API_I_EK_START + API_I_EK_LENGTH]
    return l_pcc, l_id, l_rw, l_ek

# ==================== DEVICE OPERATIONS ====================
def do_bist():
    l_r = do_transfer_l(assemble_bist_l())
    b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s = disassemble_l_bist(l_r)
    return b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s

def do_id(l_pccid_s):
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    l_pccid = l_pcc + l_id
    b_pass_id = 1 if l_pccid == l_pccid_s else 0
    return b_pass_id, l_pcc, l_id

def do_rw(l_pccid, l_rw_s):
    l_r = do_transfer_l(assemble_cw_l(l_pccid))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    b_pass_rw = 1 if l_rw == l_rw_s else 0
    return b_pass_rw, l_pcc, l_id, l_rw

def do_ek(l_pccid_bar, l_ek_s):
    l_r = do_transfer_l(assemble_ek_l(l_pccid_bar))
    l_pcc, l_id, l_ek = disassemble_l_ek(l_r)
    b_pass_ek = 1 if l_ek == l_ek_s else 0
    return b_pass_ek, l_pcc, l_id, l_ek

def get_pccid():
    """Get PCCID from device"""
    l_r = do_transfer_l(assemble_id_l())
    l_pcc, l_id = disassemble_l_id(l_r)
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    return s_pcc + s_id

def do_rw_only(cw_l):
    """Get RW from device given CW"""
    l_r = do_transfer_l(assemble_cw_l(cw_l))
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    s_rw = ''.join('%02x' % e for e in l_rw)
    return s_rw

def do_rw_ek(cw_l):
    """Get RW and EK from device"""
    l_r = do_transfer_l(assemble_ek_l(cw_l))
    l_pcc, l_id, l_rw, l_ek = disassemble_l_rwek(l_r)
    s_rw = ''.join('%02x' % e for e in l_rw)
    s_ek = ''.join('%02x' % e for e in l_ek)
    return s_rw, s_ek

# ==================== CYBERROCK API FUNCTIONS ====================
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):
    response = requests.post(cyberrock_iot_login,
        headers=cloudflaretokens,
        data={'username': iotusername, 'password': iotpassword},
        timeout=10)
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
    max_attempts = 30
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.2)
        response = requests.get(cyberrock_iot_checkstatus,
            headers=data_auth, params=params_post, json=data_post, timeout=10)
        responsedata = response.json()
        authenticationresult = responsedata['status']
        attempt += 1
    
    claimid = responsedata.get('claimId', '') if authenticationresult == 'CLAIM_ID' else ''
    return authenticationresult, claimid

def do_immediate_auth(cloudflaretokens, accesstoken, PCCID, CW, RW):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"PCCID": PCCID, "CW": CW, "RW": RW}
    response = requests.post(cyberrock_iot_immediateauth,
        headers=data_auth, data=data_post, timeout=10)
    responsedata = response.json()
    return responsedata['status']

def do_request_rw_transactionid(cloudflaretokens, accesstoken, PCCID, CW):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"PCCID": PCCID, "CW": CW}
    response = requests.post(cyberrock_iot_requestRWtransactionid,
        headers=data_auth, data=data_post, timeout=10)
    tiddata = response.json()
    return tiddata['transactionId']

def do_request_rw(cloudflaretokens, accesstoken, PCCID, CW, transactionid):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"PCCID": PCCID, "CW": CW, "transactionId": transactionid}
    response = requests.post(cyberrock_iot_requestRW,
        headers=data_auth, data=data_post, timeout=10)
    tiddata = response.json()
    return tiddata['rwTransactionId']

def do_request_rw_status(cloudflaretokens, accesstoken, RWtransactionID):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    result = 'NOT_READY'
    max_attempts = 30
    attempt = 0
    
    while result == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.2)
        response = requests.get(cyberrock_iot_requestRWstatus,
            headers=data_auth, params={"rwTransactionId": RWtransactionID}, timeout=10)
        responsedata = response.json()
        result = responsedata['status']
        attempt += 1
    
    rw = responsedata.get('RW', '') if result == 'GENERATED_RW' else ''
    return result, rw

def do_cyberrock_tenant_login(cloudflaretokens, tenantusername, tenantpassword):
    response = requests.post(cyberrock_tenant_login,
        headers=cloudflaretokens,
        data={'email': tenantusername, 'password': tenantpassword},
        timeout=10)
    logindata = response.json()
    return logindata['accessToken']

def do_cyberrock_tenant_claimid(cloudflaretokens, tenantaccesstoken, claimid):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + tenantaccesstoken}
    response = requests.post(cyberrock_tenant_claimid,
        headers=data_auth, data={'claimId': claimid}, timeout=10)
    responsedata = response.json()
    return responsedata['result']

def get_cyberrock_cw_ek(cloudflaretokens, accesstoken, PCCID):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"PCCID": PCCID}
    response = requests.post(cyberrock_iot_requestcwek,
        headers=data_auth, data=data_post, timeout=10)
    cwdata = response.json()
    return cwdata['CW'], cwdata['transactionId']

def do_submit_rw_ek(cloudflaretokens, accesstoken, PCCID, CW, RW, transactionid):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    data_post = {"PCCID": PCCID, "CW": CW, "RW": RW, "transactionId": transactionid}
    response = requests.post(cyberrock_iot_replyrwek,
        headers=data_auth, data=data_post, timeout=10)
    cwdata = response.json()
    return cwdata['transactionId']

def do_retrieve_result_ek(cloudflaretokens, accesstoken, transactionid):
    data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}
    authenticationresult = 'NOT_READY'
    max_attempts = 30
    attempt = 0
    
    while authenticationresult == 'NOT_READY' and attempt < max_attempts:
        time.sleep(0.2)
        response = requests.get(cyberrock_iot_checkstatusek,
            headers=data_auth,
            params={'transactionId': transactionid, "requestSignedResponse": "True"},
            timeout=10)
        responsedata = response.json()
        authenticationresult = responsedata['status']
        attempt += 1
    
    ekresult = responsedata.get('ek', '')
    claimid = responsedata.get('claimId', '') if authenticationresult == 'CLAIM_ID' else ''
    return authenticationresult, claimid, ekresult

# ==================== INITIALIZATION ====================
def gpio_setup():
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        if interface == 'SPI':
            GPIO.setup(API_CS1, GPIO.OUT)
            GPIO.setup(API_CS2, GPIO.OUT)
            GPIO.setup(API_CS3, GPIO.OUT)
            GPIO.output(API_CS1, GPIO.HIGH)
            GPIO.output(API_CS2, GPIO.HIGH)
            GPIO.output(API_CS3, GPIO.HIGH)
    except:
        pass

gpio_setup()
print(f"[SGA] Module loaded - Environment: {environment}, Interface: {interface}")
