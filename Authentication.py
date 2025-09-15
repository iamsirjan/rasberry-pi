import RPi.GPIO as GPIO 
import  sys
from functools import reduce
from math import log,ceil
 
 
sys.path.insert(1, '/home/pi/SandGrain/SandGrainSuite_USB/')

import sga as sga
import SandGrain_Credentials as credentials


API_G   =  5 # GPIO5 , Board pin 29
API_R   =  6 # GPIO6 , Board pin 31
API_Y   = 12 # GPIO12, Board pin 32

def gpio_setup():
    GPIO.setmode(GPIO.BCM)
    
    sga.gpio_setup()
    
    GPIO.setup(API_G  , GPIO.OUT)
    GPIO.setup(API_R  , GPIO.OUT)
    GPIO.setup(API_Y  , GPIO.OUT)
    
    GPIO.output(API_G  , GPIO.LOW)
    GPIO.output(API_R  , GPIO.LOW)
    GPIO.output(API_Y  , GPIO.HIGH)
 
def gpio_gry_set(i_g, i_r, i_y):
    GPIO.output(API_G, i_g)
    GPIO.output(API_R, i_r)
    GPIO.output(API_Y, i_y)
 
def listToInt(lst):
    """Convert a byte list into a number"""
    return reduce(lambda x,y:(x<<8)+y,lst)

def intToList(number):
    """Converts an integer of any length into an integer list"""
    L1 = log(number,256)
    L2 = ceil(L1)
    if L1 == L2:
        L2 += 1
    return [(number&(0xff<<8*i))>>8*i for i in reversed(range(L2))] 
 
def main():

    GPIO.setwarnings(False)
    gpio_setup()
    

    pccid = sga.get_pccid()

    iotaccesstoken, iotid = sga.do_cyberrock_iot_login(credentials.cloudflaretokens, credentials.iotusername, credentials.iotpassword)
    
    cw, transactionid = sga.get_cyberrock_cw(credentials.cloudflaretokens, iotaccesstoken, pccid, False)
    
    rw = sga.do_rw_only(intToList(int(cw,16)))
    
    transactionidresponse = sga.do_submit_rw(credentials.cloudflaretokens, iotaccesstoken, pccid, cw, rw, transactionid, False)
    
    authenticationresult, claimid = sga.do_retrieve_result(credentials.cloudflaretokens, iotaccesstoken, transactionid, False)
    
    claimresult = ""
    
    if (authenticationresult == 'CLAIM_ID' or authenticationresult == 'AUTH_OK'):
        gpio_gry_set(1, 0, 0)
    else:
        gpio_gry_set(0, 1, 0)
    
    print('\n')   
    
main()    