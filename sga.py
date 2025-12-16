import random, sys, time, requests
import spidev, serial
import RPi.GPIO as GPIO
from periphery import SPI
from functools import reduce
from math import log, ceil
import threading
import glob
import queue
from contextlib import contextmanager

# Device configuration class
class DeviceConfig:
    def __init__(self, device_id, interface='USB', serial_port=None, cs_pin=None):
        self.device_id = device_id
        self.interface = interface
        self.serial_port = serial_port
        self.cs_pin = cs_pin
        self.lock = threading.Lock()
        self.is_busy = False
        
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

# Context manager for serial port
@contextmanager
def serial_connection(device, timeout=2.0):
    """Context manager for safe serial port handling"""
    ser = None
    try:
        ser = serial.Serial(
            device.serial_port,
            115200,
            timeout=timeout,
            write_timeout=timeout,
            inter_byte_timeout=0.1
        )
        # Give device time to initialize
        time.sleep(0.05)
        # Flush buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        yield ser
    except serial.SerialException as e:
        print(f"[Device {device.device_id}] Serial error on {device.serial_port}: {e}")
        raise
    finally:
        if ser and ser.is_open:
            try:
                ser.close()
            except:
                pass

def do_ser_transfer_l(device, l, max_retries=3):
    """Thread-safe serial transfer with timeout and retry"""
    
    for attempt in range(max_retries):
        with device.lock:
            try:
                device.is_busy = True
                
                with serial_connection(device, timeout=2.0) as ser:
                    # Send command
                    l_s = ''.join('%02x' % e for e in l) + "\r"
                    bytes_written = ser.write(l_s.encode('utf-8'))
                    ser.flush()
                    
                    # Wait briefly for device to process
                    time.sleep(0.05)
                    
                    # Read response with timeout
                    resp_s = b''
                    start_time = time.time()
                    timeout = 2.0
                    
                    while time.time() - start_time < timeout:
                        if ser.in_waiting > 0:
                            chunk = ser.read(ser.in_waiting)
                            resp_s += chunk
                            # Check if we got a complete response (should end with data)
                            if len(resp_s) >= 10:  # Minimum expected response size
                                break
                        else:
                            time.sleep(0.01)
                    
                    if len(resp_s) == 0:
                        raise Exception(f"No data received (attempt {attempt + 1}/{max_retries})")
                    
                    # Parse response
                    try:
                        l_r = [int(resp_s[i:i+2], 16) for i in range(0, len(resp_s)-1, 2)]
                    except ValueError as e:
                        raise Exception(f"Invalid response format: {resp_s[:20]}")
                    
                    print(f"[Device {device.device_id}] Transfer successful ({len(l_r)} bytes)")
                    return l_r
                    
            except Exception as e:
                print(f"[Device {device.device_id}] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.2)  # Wait before retry
            finally:
                device.is_busy = False
                time.sleep(0.1)  # Small delay between operations

def spi_open():
    spi = spidev.SpiDev(0, 0)
    spi.max_speed_hz = 10_000_000
    return spi

def spi_close(spi):
    if spi:
        spi.close()

def do_spi_transfer_l(device, l, max_retries=3):
    """Thread-safe SPI transfer with retry"""
    
    for attempt in range(max_retries):
        with device.lock:
            spi = None
            try:
                device.is_busy = True
                GPIO.output(device.cs_pin, GPIO.LOW)
                time.sleep(0.01)
                
                spi = spi_open()
                l_r = spi.xfer(l)
                
                print(f"[Device {device.device_id}] Transfer successful")
                return l_r
                
            except Exception as e:
                print(f"[Device {device.device_id}] Attempt {attempt + 1}/{max_retries} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(0.1)
            finally:
                if spi:
                    spi_close(spi)
                GPIO.output(device.cs_pin, GPIO.HIGH)
                device.is_busy = False
                time.sleep(0.05)

def do_transfer_l(device, l):
    """Perform transfer based on device interface type"""
    if device.interface == 'SPI':
        return do_spi_transfer_l(device, l)
    elif device.interface == 'USB':
        return do_ser_transfer_l(device, l)
    else:
        raise ValueError(f"Unknown interface: {device.interface}")

# Command assembly functions (from original code)
def assemble_id_l():
    return [0x01, 0x00, 0x00, 0x00] + [0] + [0]*32

def assemble_cw_l(l_challenge):
    return [0x03, 0x00, 0x08, 0x00] + [0] + l_challenge + [0] + [0]*49

def assemble_ek_l(l_challenge):
    return [0x07, 0x00, 0x08, 0x00] + [0] + l_challenge + [0] + [0]*65

# Get PCCID for specific device
def get_pccid(device):
    """Get PCCID for specific device"""
    print(f"[Device {device.device_id}] Getting PCCID...")
    
    l = assemble_id_l()
    l_r = do_transfer_l(device, l)
    
    l_pcc = l_r[5:21]
    l_id = l_r[21:37]
    
    s_pcc = ''.join('%02x' % e for e in l_pcc)
    s_id = ''.join('%02x' % e for e in l_id)
    s_pcc_id = s_pcc + s_id
    
    print(f"[Device {device.device_id}] PCCID: {s_pcc_id}")
    return s_pcc_id

def get_rw_only(device, cw_l):
    """Get RW response from device"""
    print(f"[Device {device.device_id}] Getting RW...")
    
    l = assemble_cw_l(cw_l)
    l_r = do_transfer_l(device, l)
    
    l_rw = l_r[71:87]
    s_rw = ''.join('%02x' % e for e in l_rw)
    
    print(f"[Device {device.device_id}] RW: {s_rw}")
    return s_rw

# Worker function for queue-based processing
def device_worker(device, task_queue, result_queue):
    """Worker thread that processes tasks from queue"""
    print(f"[Device {device.device_id}] Worker started")
    
    while True:
        try:
            task = task_queue.get(timeout=1.0)
            
            if task is None:  # Poison pill to stop worker
                print(f"[Device {device.device_id}] Worker stopping")
                break
            
            task_id, func, args = task
            
            try:
                print(f"[Device {device.device_id}] Starting task {task_id}")
                result = func(device, *args)
                result_queue.put((device.device_id, task_id, True, result))
                print(f"[Device {device.device_id}] Task {task_id} completed")
            except Exception as e:
                print(f"[Device {device.device_id}] Task {task_id} failed: {e}")
                result_queue.put((device.device_id, task_id, False, str(e)))
            
            task_queue.task_done()
            
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[Device {device.device_id}] Worker error: {e}")

def process_devices_with_queue(devices, tasks):
    """
    Process multiple devices using task queues
    tasks = [(task_id, function, args), ...]
    """
    task_queue = queue.Queue()
    result_queue = queue.Queue()
    workers = []
    
    # Start worker thread for each device
    for device in devices:
        worker = threading.Thread(
            target=device_worker,
            args=(device, task_queue, result_queue),
            daemon=True
        )
        worker.start()
        workers.append(worker)
    
    # Add tasks to queue
    for task in tasks:
        task_queue.put(task)
    
    # Wait for all tasks to complete
    task_queue.join()
    
    # Stop workers
    for _ in workers:
        task_queue.put(None)
    
    for worker in workers:
        worker.join(timeout=2.0)
    
    # Collect results
    results = []
    while not result_queue.empty():
        results.append(result_queue.get())
    
    return results

def process_devices_sequential(devices, operation_func, *args):
    """Process devices one at a time (safest method)"""
    results = []
    
    for device in devices:
        try:
            print(f"\n[Device {device.device_id}] Starting operation...")
            result = operation_func(device, *args)
            results.append((device.device_id, True, result))
            print(f"[Device {device.device_id}] Operation completed successfully")
            
            # Add delay between devices to prevent conflicts
            time.sleep(0.2)
            
        except Exception as e:
            print(f"[Device {device.device_id}] Operation failed: {e}")
            results.append((device.device_id, False, str(e)))
    
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
        
        print(f"\nFound {len(devices)} device(s)\n")
        
        # Method 1: Sequential processing (RECOMMENDED for stability)
        print("=== Sequential Processing ===")
        results = process_devices_sequential(devices, get_pccid)
        
        print("\n=== Results ===")
        for device_id, success, result in results:
            if success:
                print(f"Device {device_id}: SUCCESS - PCCID = {result}")
            else:
                print(f"Device {device_id}: FAILED - {result}")
        
        # Method 2: Queue-based processing (for complex workflows)
        print("\n\n=== Queue-based Processing ===")
        tasks = [
            (f"task_{i}", get_pccid, ()) for i in range(len(devices))
        ]
        results = process_devices_with_queue(devices, tasks)
        
        print("\n=== Results ===")
        for device_id, task_id, success, result in results:
            if success:
                print(f"Device {device_id} ({task_id}): SUCCESS - {result}")
            else:
                print(f"Device {device_id} ({task_id}): FAILED - {result}")
        
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        GPIO.cleanup()
