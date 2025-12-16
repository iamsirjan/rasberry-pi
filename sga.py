import random, sys, time, requests
import spidev, serial
import RPi.GPIO as GPIO
from periphery import SPI
from functools import reduce
from math import log, ceil
import threading
import glob

# Device configuration class
class DeviceConfig:
    def __init__(self, device_id, interface='USB', serial_port=None, cs_pin=None):
        self.device_id = device_id
        self.interface = interface
        self.serial_port = serial_port
        self.cs_pin = cs_pin
        self.lock = threading.Lock()
        
# Auto-detect available serial ports
def detect_serial_ports():
    """Detect all available USB serial ports"""
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    return sorted(ports)

# Initialize multiple devices
def initialize_devices(num_devices=None, interface='USB'):
    """Initialize multiple device configurations"""
    devices = []
    
    if interface == 'USB':
        ports = detect_serial_ports()
        if num_devices:
            ports = ports[:num_devices]
        
        print(f"Found {len(ports)} serial ports: {ports}")
        
        for idx, port in enumerate(ports):
            device = DeviceConfig(
                device_id=idx,
                interface='USB',
                serial_port=port
            )
            devices.append(device)
            print(f"Device {idx}: {port}")
    
    elif interface == 'SPI':
        # For SPI, use different CS pins for each device
        cs_pins = [22, 27, 17]  # GPIO22, GPIO27, GPIO17
        if num_devices:
            cs_pins = cs_pins[:num_devices]
        
        for idx, cs_pin in enumerate(cs_pins):
            device = DeviceConfig(
                device_id=idx,
                interface='SPI',
                cs_pin=cs_pin
            )
            devices.append(device)
            GPIO.setup(cs_pin, GPIO.OUT)
            GPIO.output(cs_pin, GPIO.HIGH)
            print(f"Device {idx}: CS Pin {cs_pin}")
    
    return devices

# Modified serial functions with device-specific port
def ser_open(device):
    """Open serial port for specific device"""
    try:
        ser = serial.Serial(
            device.serial_port, 
            115200, 
            timeout=0.5,
            write_timeout=1.0
        )
        time.sleep(0.1)  # Give device time to initialize
        return ser
    except serial.SerialException as e:
        print(f"Error opening {device.serial_port}: {e}")
        raise

def ser_close(ser):
    """Close serial port safely"""
    try:
        if ser and ser.is_open:
            ser.close()
    except Exception as e:
        print(f"Error closing serial port: {e}")

def do_ser_transfer_l(device, l):
    """Thread-safe serial transfer for specific device"""
    with device.lock:
        ser = None
        try:
            ser = ser_open(device)
            
            # Flush any existing data
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # Send command
            l_s = ''.join('%02x' % e for e in l) + "\r"
            ser.write(l_s.encode('utf-8'))
            ser.flush()
            
            # Read response with timeout handling
            resp_s = ser.read(512)
            
            if len(resp_s) == 0:
                raise Exception("No data received from device")
            
            # Parse response
            l_r = [int(resp_s[i:i+2], 16) for i in range(0, len(resp_s)-1, 2)]
            
            return l_r
            
        except Exception as e:
            print(f"Device {device.device_id} error: {e}")
            raise
        finally:
            if ser:
                ser_close(ser)

# Modified SPI functions with device-specific CS pin
def spi_open():
    spi = spidev.SpiDev(0, 0)
    spi.max_speed_hz = 10_000_000
    return spi

def spi_close(spi):
    if spi:
        spi.close()

def do_spi_transfer_l(device, l):
    """Thread-safe SPI transfer for specific device"""
    with device.lock:
        spi = None
        try:
            GPIO.output(device.cs_pin, GPIO.LOW)
            spi = spi_open()
            l_r = spi.xfer(l)
            return l_r
        finally:
            if spi:
                spi_close(spi)
            GPIO.output(device.cs_pin, GPIO.HIGH)

# Unified transfer function
def do_transfer_l(device, l):
    """Perform transfer based on device interface type"""
    if device.interface == 'SPI':
        return do_spi_transfer_l(device, l)
    elif device.interface == 'USB':
        return do_ser_transfer_l(device, l)
    else:
        raise ValueError(f"Unknown interface: {device.interface}")

# Example: Get PCCID for specific device
def get_pccid(device):
    """Get PCCID for specific device"""
    l_command_ident = [0x01, 0x00, 0x00, 0x00]
    l = l_command_ident + [0] + [0]*32
    
    l_r = do_transfer_l(device, l)
    
    l_pcc = l_r[5:21]
    l_id = l_r[21:37]
    
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    s_pcc_id = s_pcc + s_id
    
    return s_pcc_id

# Example: Parallel device operations
def process_device(device, operation_func, *args):
    """Process single device operation"""
    try:
        print(f"Processing device {device.device_id} on {device.serial_port or device.cs_pin}")
        result = operation_func(device, *args)
        print(f"Device {device.device_id} completed successfully")
        return (device.device_id, True, result)
    except Exception as e:
        print(f"Device {device.device_id} failed: {e}")
        return (device.device_id, False, str(e))

def process_devices_parallel(devices, operation_func, *args):
    """Process multiple devices in parallel using threads"""
    threads = []
    results = []
    
    def worker(device):
        result = process_device(device, operation_func, *args)
        results.append(result)
    
    # Start threads
    for device in devices:
        thread = threading.Thread(target=worker, args=(device,))
        thread.start()
        threads.append(thread)
    
    # Wait for completion
    for thread in threads:
        thread.join()
    
    return results

def process_devices_sequential(devices, operation_func, *args):
    """Process multiple devices sequentially"""
    results = []
    for device in devices:
        result = process_device(device, operation_func, *args)
        results.append(result)
    return results

# Main execution example
if __name__ == "__main__":
    try:
        # Initialize GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        # Detect and initialize devices
        print("Detecting devices...")
        devices = initialize_devices(interface='USB')
        
        if not devices:
            print("No devices found!")
            sys.exit(1)
        
        print(f"\nFound {len(devices)} device(s)")
        
        # Process all devices in parallel
        print("\nReading PCCID from all devices...")
        results = process_devices_parallel(devices, get_pccid)
        
        # Display results
        print("\n=== Results ===")
        for device_id, success, result in results:
            if success:
                print(f"Device {device_id}: PCCID = {result}")
            else:
                print(f"Device {device_id}: FAILED - {result}")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        GPIO.cleanup()
