import random, sys, time, requests
import spidev, serial
import RPi.GPIO as GPIO 
from periphery import SPI
from functools import reduce
from math import log,ceil

environment = 'UAT'
#environment = 'SANDBOX'

#interface = 'SPI'
interface = 'USB'


if(environment == 'UAT'):
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

if(environment == 'SANDBOX'):
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

i_f = 10_000_000 #10MHz

fp_out = None

API_CS1 = 22 # GPIO22,  
API_CS2 = 27 # GPIO23,  
API_CS3 = 17 # GPIO24,  

l_command_ident = [0x01, 0x00, 0x00, 0x00]
l_command_bist  = [0x80, 0x00, 0x00, 0x00] #pv-2024-03-13: only 0x80 is ok
l_command_cr    = [0x03, 0x00, 0x08, 0x00]
l_command_cr_ek = [0x07, 0x00, 0x08, 0x00]

API_I_IDENT_PART1_START  =  5
API_I_IDENT_PART1_LENGTH = 16
API_I_IDENT_PART2_START  = 21 # API_I_IDENT_PART1_START + API_I_IDENT_PART1_LENGTH
API_I_IDENT_PART2_LENGTH = 16

API_I_IDENT_START        =  5
API_I_IDENT_LENGTH       = 32

API_I_CHAL_START         = 38 # API_I_IDENT_START       + API_I_IDENT_LENGTH        + 1
API_I_CHAL_LENGTH        = 32                                                       
                                                                                    
API_I_CHAL_PART1_START   = 38 # API_I_IDENT_START       + API_I_IDENT_LENGTH        + 1
API_I_CHAL_PART1_LENGTH  = 16                                                       
API_I_CHAL_PART2_START   = 54 # API_I_CHAL_PART1_START  + API_I_CHAL_PART1_LENGTH   
API_I_CHAL_PART2_LENGTH  = 16                                                       
                                                                                    
API_I_RESP_START         = 71 # API_I_CHAL_START        + API_I_CHAL_LENGTH         + 1
API_I_RESP_LENGTH        = 16                                                       
API_I_EK_START           = 87 # API_I_RESP_START        + API_I_RESP_LENGTH         
API_I_EK_LENGTH          = 16                                                       
                                                                                    
API_I_RWL_PART1_START    = 38 # API_I_IDENT_START       + API_I_IDENT_LENGTH        + 1
API_I_RWL_PART1_LENGTH   = 16
API_I_RWL_PART2_START    = 54 # API_I_RWL_PART1_START   + API_I_RWL_PART1_LENGTH
API_I_RWL_PART2_LENGTH   = 16

API_I_BIST               = 71

def gpio_setup():
    GPIO.setup(API_CS1, GPIO.OUT)   
    GPIO.setup(API_CS2, GPIO.OUT)   
    GPIO.setup(API_CS3, GPIO.OUT)   
    GPIO.output(API_CS1, GPIO.HIGH)
    GPIO.output(API_CS2, GPIO.HIGH)
    GPIO.output(API_CS3, GPIO.HIGH)

#USB serial

def ser_open():
    # NOTE the user must ensure that the serial port and baudrate are correct
    serPort = "/dev/ttyACM0"
    baudRate = 115200
    ser = serial.Serial(serPort, baudRate, timeout=0.5)
    #print ("Serial port " + serPort + " opened  Baudrate " + str(baudRate))
    return ser

def ser_close(ser):
    ser.close()

def do_ser_transfer_l(l):
    ser = ser_open()
    l_s = ''.join('%02x' % e for e in l) + "\r"
    ser.write(l_s.encode('utf-8'))
    resp_s = ser.read(512)
    #print(resp_s)
    ser_close(ser)
    l_r = [int(resp_s[i:i+2], 16) for i in range(0, len(resp_s)-1, 2)]
    #print(l_r)
    return l_r

#spi
def spi_open():
    spi = spidev.SpiDev(0, 0) # bus, device
    spi.max_speed_hz = i_f 
    return spi
    
def spi_close(spi): spi.close()

def do_spi_transfer_l(l):
    GPIO.output(API_CS1, GPIO.LOW)
    
    spi = spi_open()
    l_r = spi.xfer(l) #xfer2
    spi_close(spi)
    
    GPIO.output(API_CS1, GPIO.HIGH)
    print(l_r)
    return l_r


if(interface == 'SPI'):
    do_transfer_l = do_spi_transfer_l

if(interface == 'USB'):
    do_transfer_l = do_ser_transfer_l



#helper routines
def do_print_l(s, l, end = ''): 
    print(s, ' '.join('%02x' % e for e in l), end)
    s_out = s
    s_out = s_out + ' '.join('%02x' % e for e in l)
    s_out = s_out + '\n'

def list_invert(l): return [(~e)&0xFF for e in l]    
    
#assemble
def assemble_bist_l()         : return l_command_bist +[0]*68
def assemble_id_l()           : return l_command_ident + [0] + [0]*32
def assemble_cw_l(l_challenge): return l_command_cr    + [0] + l_challenge + [0] + [0]*49 # CR 65-16
def assemble_ek_l(l_challenge): return l_command_cr_ek + [0] + l_challenge + [0] + [0]*65 # CR_EK 65

# disassemble
def disassemble_l_bist(l_r):
    l_pcc_s       = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START  + API_I_IDENT_PART1_LENGTH]                
    l_id_s        = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START  + API_I_IDENT_PART2_LENGTH]                
    l_rw_s        = l_r[API_I_RWL_PART1_START : API_I_RWL_PART1_START  + API_I_RWL_PART1_LENGTH]                
    l_ek_s        = l_r[API_I_RWL_PART2_START : API_I_RWL_PART2_START  + API_I_RWL_PART2_LENGTH]                
    i_pass        = l_r[API_I_BIST]      
    b_pass = 1 if i_pass == 0x50 else 0
    return b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s
    
def disassemble_l_id(l_r):
    l_pcc       = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START  + API_I_IDENT_PART1_LENGTH]                
    l_id        = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START  + API_I_IDENT_PART2_LENGTH]                
    return l_pcc, l_id
  
def disassemble_l_rw(l_r):
    l_pcc       = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START  + API_I_IDENT_PART1_LENGTH]                
    l_id        = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START  + API_I_IDENT_PART2_LENGTH]                
    l_rw        = l_r[API_I_RESP_START        : API_I_RESP_START         + API_I_RESP_LENGTH       ]          
    return l_pcc, l_id, l_rw

def disassemble_l_ek(l_r):
    l_pcc       = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START  + API_I_IDENT_PART1_LENGTH]                
    l_id        = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START  + API_I_IDENT_PART2_LENGTH]                
    l_ek        = l_r[API_I_EK_START          : API_I_EK_START           + API_I_EK_LENGTH         ]          
    return l_pcc, l_id, l_ek

def disassemble_l_rwek(l_r):
    l_pcc       = l_r[API_I_IDENT_PART1_START : API_I_IDENT_PART1_START  + API_I_IDENT_PART1_LENGTH]                
    l_id        = l_r[API_I_IDENT_PART2_START : API_I_IDENT_PART2_START  + API_I_IDENT_PART2_LENGTH]                
    l_rw        = l_r[API_I_RESP_START        : API_I_RESP_START         + API_I_RESP_LENGTH       ]  
    l_ek        = l_r[API_I_EK_START          : API_I_EK_START           + API_I_EK_LENGTH         ]                  
    return l_pcc, l_id, l_rw, l_ek


# print
def do_bist_print(b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s):
    print("\nbist ok = %i" % b_pass)
    do_print_l("l_pcc_s = ", l_pcc_s)
    do_print_l("l_id_s  = ", l_id_s, "\n")
    do_print_l("l_rw_s  = ", l_rw_s)
    do_print_l("l_ek_s  = ", l_ek_s, "\n")

def do_id_print(b_pass_id, l_pcc, l_id):    
    print("id ok   = %i" % b_pass_id)
    do_print_l("l_pcc   = ", l_pcc)
    do_print_l("l_id    = ", l_id, "\n")
    
def do_rw_print(b_pass_rw, l_pcc, l_id, l_rw):    
    print("cr ok   = %i" % b_pass_rw)
    do_print_l("l_rw    = ", l_rw, "\n")

def do_ek_print(b_pass_ek, l_pcc, l_id, l_ek):    
    print("ek ok   = %i" % b_pass_ek)
    do_print_l("l_ek    = ", l_ek, "\n")

# do bist/id/rw/ek
def do_bist():
    l_r = do_transfer_l(assemble_bist_l())    
    b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s = disassemble_l_bist(l_r)    
    
    #do_print_l_fp(l_pcc_s)
    #do_print_l_fp(l_id_s)
    #do_print_l_fp(l_rw_s)
    #do_print_l_fp(l_ek_s)
    
    return b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s

def do_id(l_pccid_s):
    l_r = do_transfer_l(assemble_id_l())    
    l_pcc, l_id = disassemble_l_id(l_r)
    l_pccid = l_pcc + l_id 
    
    #do_print_l_fp(l_pccid) #save l_pccid to file
    
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


def do_bist_testsuite():  
    #print('phase 1: bist check')
    b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s = do_bist()
    do_bist_print(b_pass, l_pcc_s, l_id_s, l_rw_s, l_ek_s)    
    
    l_pccid_s = l_pcc_s + l_id_s
    
    #print('phase 2: id check')
    b_pass_id, l_pcc, l_id = do_id(l_pccid_s)
    do_id_print(b_pass_id, l_pcc, l_id)    
    
    #print('phase 3: cr check')
    b_pass_rw, l_pcc, l_id, l_rw = do_rw(l_pccid_s, l_rw_s)
    do_rw_print(b_pass_rw, l_pcc, l_id, l_rw)    
    
    #print('phase 4: ek check')
    b_pass_ek, l_pcc, l_id, l_ek = do_ek(list_invert(l_pccid_s), l_ek_s)
    do_ek_print(b_pass_ek, l_pcc, l_id, l_ek)    
    
    s_pcc    = ''.join('%02x' % e for e in l_pcc_s)
    s_id     = ''.join('%02x' % e for e in l_id_s)
    s_pcc_id = s_pcc + s_id
    
    return b_pass, b_pass_id, b_pass_rw, b_pass_ek, s_pcc_id


#get PCID only
def get_pccid():
    l_r = do_transfer_l(assemble_id_l())    
    l_pcc, l_id = disassemble_l_id(l_r)
    l_pccid = l_pcc + l_id
    s_pcc    = ''.join('%02x' % e for e in l_pcc)
    s_id     = ''.join('%02x' % e for e in l_id)
    s_pcc_id = s_pcc + s_id
 
    return s_pcc_id


#get RW only
def do_rw_only(cw_l):    
    l_r = do_transfer_l(assemble_cw_l(cw_l))    
    l_pcc, l_id, l_rw = disassemble_l_rw(l_r)
    s_rw = ''.join('%02x' % e for e in l_rw)
    return s_rw

#get RW and EK
def do_rw_ek(cw_l):    
    l_r = do_transfer_l(assemble_ek_l(cw_l))    
    l_pcc, l_id, l_rw, l_ek = disassemble_l_rwek(l_r)        
    s_rw = ''.join('%02x' % e for e in l_rw)
    s_ek = ''.join('%02x' % e for e in l_ek)
    return s_rw, s_ek


#CyberRock
def do_cyberrock_iot_login(cloudflaretokens, iotusername, iotpassword):

	print("Logging in to CyberRock IoT portal")

	response = requests.post(cyberrock_iot_login,
	# headers={'accept: application/json', 'Content-Type: application/json'},
	 headers = cloudflaretokens,
	 data = {'username': iotusername, 'password': iotpassword},
	 )


	print(response.status_code)
	#print(response.json())
	print('')

	logindata = response.json()

	accesstoken = (logindata['accessToken'])
	iotid = (logindata['iotId'])

	return accesstoken, iotid
	

def get_cyberrock_cw(cloudflaretokens, accesstoken, PCCID, requestSignature):

	print("Retrieving CW from CyberRock")

	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {"requestSignedResponse": requestSignature,
			"PCCID": PCCID
		    }

	response = requests.post(cyberrock_iot_requestcw,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	 headers = data_auth, json = data_post,
	 )
	 
	print(response.url)
	print(response.status_code)
	print(response.json())
	print('')

	cwdata = response.json()

	CW = cwdata['CW']
	transactionid = cwdata['transactionId']
	
	return CW, transactionid
	
def do_submit_rw(cloudflaretokens, accesstoken, PCCID, CW, RW, transactionid, requestSignature):

	print("Submitting RW to CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {
		"requestSignedResponse": requestSignature,
		"PCCID": PCCID,
		"CW": CW,
		"RW": RW,
		"transactionId": transactionid	
			}

	response = requests.post(cyberrock_iot_replyrw,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	 headers = data_auth, json = data_post,
	 )
	 
	print(response.url)
	print(response.status_code)
	print(response.json())

	print('')
	
	cwdata = response.json()

	transactionid = cwdata['transactionId']
	
	return transactionid

def do_retrieve_result(cloudflaretokens, accesstoken, transactionid, requestSignature):	
	print("Retrieving result from CyberRock")

	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	authenticationresult = 'NOT_READY'
	
	params_post = {"transactionId": transactionid}
	
	data_post = {		 
		"requestSignedResponse": requestSignature}

	while (authenticationresult == 'NOT_READY'):
		
		time.sleep(0.1)
		
		response = requests.get(cyberrock_iot_checkstatus,
		# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
		 headers = data_auth, params = params_post, json = data_post,
		 )

		print(response.url)
		print(response.status_code)
		print(response.json())
		print('\n')
		
		responsedata = response.json()
		authenticationresult = responsedata['status']
		
	if (authenticationresult == 'CLAIM_ID'):	
		claimid = responsedata['claimId']
	else:
		claimid = ''
		
	return authenticationresult, claimid

def do_cyberrock_tenant_login(cloudflaretokens, tenantusername, tenantpassword):

	print("Logging in to CyberRock Tenant portal")

	response = requests.post(cyberrock_tenant_login,
	# headers={'accept: application/json', 'Content-Type: application/json'},
	 headers = cloudflaretokens,
	 data = {'email': tenantusername, 'password': tenantpassword},
	 )

	print(response.status_code)
	print(response.json())
	print('')

	logindata = response.json()

	tenantaccesstoken = (logindata['accessToken'])

	return tenantaccesstoken
	

def do_cyberrock_tenant_claimid(cloudflaretokens, tenantaccesstoken, claimid):

	print("Claiming ID in CyberRock Tenant portal")

	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + tenantaccesstoken}

	response = requests.post(cyberrock_tenant_claimid,
	# headers={'accept: application/json', 'Content-Type: application/json'},
	 headers = data_auth,
	 data = {'claimId': claimid}
	 )

	print(response.status_code)
	print(response.json())
	print('')

	responsedata = response.json()
	result = (responsedata['result'])

	return result
	
def do_immediate_auth(cloudflaretokens, accesstoken, PCCID, CW, RW):

	print("Submitting CW,RW to CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {
		"PCCID": PCCID,
		"CW": CW,
		"RW": RW
		}

	response = requests.post(cyberrock_iot_immediateauth,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	headers = data_auth, data = data_post,
	)

	print(response.url)
	print(response.status_code)
	print(response.json())

	print('')
	
	responsedata = response.json()
	result = (responsedata['status'])

	
	return result

def do_request_rw_transactionid(cloudflaretokens, accesstoken, PCCID, CW):

	print("Requesting TransationID from CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {
		"PCCID": PCCID,
		"CW": CW
		}

	response = requests.post(cyberrock_iot_requestRWtransactionid,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	headers = data_auth, data = data_post,
	)

	print(response.url)
	print(response.status_code)
	print(response.json())

	print('')
	
	tiddata = response.json()

	transactionid = tiddata['transactionId']
	
	return transactionid


def do_request_rw(cloudflaretokens, accesstoken, PCCID, CW, transactionid):

	print("Submitting PCCID, CW to CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {
		"PCCID": PCCID,
		"CW": CW,
		"transactionId": transactionid
		}

	response = requests.post(cyberrock_iot_requestRW,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	headers = data_auth, data = data_post,
	)

	print(response.url)
	print(response.status_code)
	print(response.json())

	print('')
	
	tiddata = response.json()

	transactionid = tiddata['rwTransactionId']
	
	return transactionid


def do_request_rw_status(cloudflaretokens, accesstoken, RWtransactionID):

	print("Retrieving result from CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	result = 'NOT_READY'

	while (result == 'NOT_READY'):
		
		time.sleep(0.1)
		
		response = requests.get(cyberrock_iot_requestRWstatus,
		# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
		headers = data_auth, params = {"rwTransactionId": RWtransactionID},
		)

		print(response.url)
		print(response.status_code)
		print(response.json())

		print('')
			
		responsedata = response.json()
		result = responsedata['status']
		
	if (result == 'GENERATED_RW'):	
		rw = responsedata['RW']
	else:
		rw = ''
			
	return result, rw


def get_cyberrock_cw_ek(cloudflaretokens, accesstoken, PCCID):

	print("Retrieving CW from CyberRock")

	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {"PCCID": PCCID}

	response = requests.post(cyberrock_iot_requestcwek,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	 headers = data_auth, data = data_post,
	 )
	 
	print(response.url)
	print(response.status_code)
	print(response.json())
	print('')

	cwdata = response.json()

	CW = cwdata['CW']
	transactionid = cwdata['transactionId']
	
	return CW, transactionid
	
def do_submit_rw_ek(cloudflaretokens, accesstoken, PCCID, CW, RW, transactionid):

	print("Submitting RW to CyberRock")
	
	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	data_post = {
		"PCCID": PCCID,
		"CW": CW,
		"RW": RW,
		"transactionId": transactionid	
			}

	response = requests.post(cyberrock_iot_replyrwek,
	# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
	 headers = data_auth, data = data_post,
	 )
	 
	print(response.url)
	print(response.status_code)
	print(response.json())

	print('')
	
	cwdata = response.json()

	transactionid = cwdata['transactionId']
	
	return transactionid

def do_retrieve_result_ek(cloudflaretokens, accesstoken, transactionid):	
	print("Retrieving result from CyberRock")

	data_auth = cloudflaretokens | {'Authorization': 'Bearer ' + accesstoken}

	authenticationresult = 'NOT_READY'

	while (authenticationresult == 'NOT_READY'):
		
		time.sleep(0.1)
		
		response = requests.get(cyberrock_iot_checkstatusek,
		# headers={'accept': 'application/json', 'Content-Type': 'application/json'},
		 headers = data_auth, params = {'transactionId': transactionid, "requestSignedResponse": "True"},
		 )

		print(response.url)
		print(response.status_code)
		print(response.json())
		print('\n')
		
		responsedata = response.json()
		authenticationresult = responsedata['status']
		
	ekresult = responsedata['ek']
		
	if (authenticationresult == 'CLAIM_ID'):	
		claimid = responsedata['claimId']
	else:
		claimid = ''
		
	return authenticationresult, claimid, ekresult