# main.py
#pylint: disable=import-error,unused-import
import time
import machine

# Step 2: Wi-Fi fallback check
try:
    with open("wifi_status.flag", "r") as f:
        status = f.read().strip()
except:
    status = "fail"

if status == "fail":
    print("[MAIN] Wi-Fi failed. Starting AP config mode...")
    import wifi_config
    wifi_config.start_access_point()
    wifi_config.start_config_server()
    # ⛔️ Stop further code from running
    raise SystemExit()

# ✅ If we reach this point, Wi-Fi is connected
print("[MAIN] Wi-Fi connected. Proceeding to main program...")

#pylint: disable=import-error,unused-import
import gc
import time
from machine import WDT, Pin, ADC
import onewire, ds18x20
import urequests
import network
import ntptime
import utime as time
from config import FIREBASE_URL, API_KEY, EMAIL, PASSWORD
relay = machine.Pin(15, machine.Pin.OUT, value=0)
relay.value(0)  # Ensure relay is off

# ---------------------------
# Global State & Configuration
# ---------------------------
# Initialize the watchdog timeela with a 10-second utut
wdt = WDT(timeout=300000)  # Timeout in milliseconds (300 sec)
current_alert_state_temp = 'normal'
current_alert_state_ph = 'normal'
last_alert_upload_time_temp = 0
last_alert_upload_time_ph = 0
token_refresh_time = 0  # Track when token was last refreshed
TOKEN_EXPIRY_TIME = 3300  # 55 minutes (in seconds)+
token = None  # Will be set after Firebase login

# Temperature daily stats
highest_temp = -100.0
lowest_temp = 100.0
temp_sum = 0.0
temp_count = 0
above_threshold_count = 0
below_threshold_count = 0
threshold_counts = {
    "morning": {"above": 0, "below": 0},
    "afternoon": {"above": 0, "below": 0},
    "evening": {"above": 0, "below": 0},
    "night": {"above": 0, "below": 0},
}

# pH daily stats
highest_ph = 0.0
lowest_ph = 14.0
ph_sum = 0.0
ph_count = 0
ph_above_threshold_count = 0
ph_below_threshold_count = 0
THRESHOLD_PH_HIGH = 8.5
THRESHOLD_PH_LOW = 7.0
ph_threshold_counts = { 
    "morning": {"above": 0, "below": 0},
    "afternoon": {"above": 0, "below": 0},
    "evening": {"above": 0, "below": 0},
    "night": {"above": 0, "below": 0},
}

# Temperature thresholds
THRESHOLD_TEMP_HIGH = 30.0
THRESHOLD_TEMP_LOW = 26.0

# ---------------------------
# Helper: Construct Timestamp
# ---------------------------
def construct_timestamp():
    current_time = time.localtime()
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
        current_time[0], current_time[1], current_time[2],
        current_time[3], current_time[4], current_time[5]
    )

# ---------------------------
# Helper: Get Last Notification
# ---------------------------
def get_last_notification(component, retries=3):
    url = f"{FIREBASE_URL}/lastNotification/{component}.json?auth={token}"
    for attempt in range(retries):
        try:
            wdt.feed()  # Feed the watchdog at the start of each attempt
            print(f"Fetching last notification for {component} (attempt {attempt + 1})")
            maybe_collect_gc()
            response = urequests.get(url, timeout=60)
            maybe_collect_gc()
            print("Free memory after fetching last notification:", gc.mem_free()) #pylint: disable=no-member
            if response.status_code == 200:
                data = response.json()  # Expecting a numeric timestamp
                response.close()
                if data is not None:
                    return data
            else:
                print(f"Status code {response.status_code} while fetching last notification for {component}")
                response.close()
        except Exception as e:
            print(f"Error fetching last notification for {component} (attempt {attempt + 1}): {e}")
            wdt.feed()
            print("Free memory during error:", gc.mem_free()) #pylint: disable=no-member

            time.sleep(1)  # Brief pause before retrying
    return 0  # Default value if unavailable


# Set the last notification timestamp for a given component
def set_last_notification(component, timestamp, retries=3):
    url = f"{FIREBASE_URL}/lastNotification/{component}.json?auth={token}"
    payload = timestamp  # This is a numeric value
    for attempt in range(retries):
        try:
            wdt.feed()  # Feed the watchdog at the start of each attempt
            print(f"[ANNOUNCE]: Setting last notification for {component} (attempt {attempt + 1})")
            maybe_collect_gc()
            response = urequests.put(url, json=payload, timeout=60)
            print("Free memory after setting last notification:", gc.mem_free()) #pylint: disable=no-member
            if response.status_code == 200:
                response.close()
                return True
            else:
                print(f"Failed to update last notification for {component}, status code: {response.status_code}")
                wdt.feed()
                maybe_collect_gc()
                response.close()
        except Exception as e:
            print(f"Error updating last notification for {component} (attempt {attempt + 1}): {e}")
            wdt.feed()
            print("Free memory during error:", gc.mem_free()) #pylint: disable=no-member
            time.sleep(1)
    return False



# Pin configuration
pH_pin = ADC(Pin(34))  # Connect pH sensor to GPIO34 (ADC1_CH6)
pH_pin.atten(ADC.ATTN_11DB)  # Set attenuation to 11dB for full range (0-3.3V)

# Calibration data
# calibration_value = 20.23
# SLOPE = -5.70  # Slope of the pH sensor
# SLOPE = -5.7
# calibration_value = 14.8432

pH1_calc = 4.01
voltage1_calc = 1.5515

pH2_calc = 6.86
voltage2_calc = 1.3490
# Slope from the two-point calibration
slope_calculation = (pH1_calc - pH2_calc) / (voltage1_calc - voltage2_calc)

SLOPE = -14.074
# calibration_value = 25.841 # GPT CALCULATION
calibration_value = 25.846 #THIS IS MY CALCULATION


# Spike and smoothing globals
last_ph = None
spike_buffer = None
ph_buffer = []
BUFFER_SIZE = 10  # You can increase if needed

def read_ph(max_retries=3):
    for attempt in range(max_retries):
        try:
            wdt.feed()
            buffer_arr = [0] * 30
            for i in range(30):
                buffer_arr[i] = pH_pin.read()
                time.sleep_ms(20)
                wdt.feed()

            buffer_arr.sort()
            avgval = sum(buffer_arr[5:25]) / 20
            voltage = avgval * (3.3 / 4095.0)
            raw_ph_func = SLOPE * voltage + calibration_value

            print("===========VOLTAGE TESTING=========")
            print(f"[DEBUG] Voltage: {voltage:.4f}")
            print(f"[DEBUG] Raw pH (uncompensated): {raw_ph_func:.3f}")

            return raw_ph_func

        except Exception as e:
            print(f"Attempt {attempt + 1}: Error reading pH sensor - {e}")
            wdt.feed()
            time.sleep(1)

            print(f"[pH ERROR]: Failed to read pH sensor after {max_retries} attempts.")
            return None
        finally:
            maybe_collect_gc()
            wdt.feed()
            
def compensate_ph_linear(ph_value, temperature_celsius, calibration_temp=25):
    """
    Compensate pH readings based on temperature.
    
    Parameters:
    - ph_value: float - the raw or uncorrected pH reading
    - temperature_celsius: float - the current water temperature in Celsius
    - calibration_temp: float - the temperature (°C) used during calibration (default: 25°C)

    Returns:
    - compensated_pH: float - temperature-compensated pH value
    """
    # pH typically changes ~0.03 units per °C from calibration temperature
    temp_difference = temperature_celsius - calibration_temp
    compensation_factor = 0.03
    compensated_ph = ph_value - (temp_difference * compensation_factor)
    return compensated_ph


def read_ph_raw_voltage():
    buffer_arr = [0] * 30
    for i in range(30):
        buffer_arr[i] = pH_pin.read()
        time.sleep_ms(20)
        wdt.feed()

    buffer_arr.sort()
    avgval = sum(buffer_arr[5:25]) / 20
    voltage = avgval * (3.3 / 4095.0)

    print("===========RAW CALIBRATION=========")
    print(f"[CALIBRATION] Voltage: {voltage:.4f} V")
    return voltage


def filter_spike(new_ph, threshold=0.2):
    global last_ph, spike_buffer

    print(f"[FILTER DEBUG] Incoming pH: {new_ph:.3f}")

    if last_ph is None:
        print("[FILTER DEBUG] No last pH yet. Accepting.")
        last_ph = new_ph
        return new_ph

    diff = abs(new_ph - last_ph)
    print(f"[FILTER DEBUG] ΔpH: {diff:.3f} (Threshold: {threshold})")

    if diff > threshold:
        if spike_buffer is None:
            print(f"[PH SPIKE?] Holding for confirmation: {new_ph:.3f}")
            spike_buffer = new_ph
            return last_ph
        elif abs(new_ph - spike_buffer) < 0.05:
            print(f"[PH VERIFIED] Confirmed spike accepted: {new_ph:.3f}")
            last_ph = new_ph
            spike_buffer = None
            return new_ph
        else:
            print(f"[PH REJECTED] Inconsistent spike: {new_ph:.3f}")
            spike_buffer = None
            return last_ph
    else:
        print("[FILTER DEBUG] pH difference is acceptable. Accepting.")
        spike_buffer = None
        last_ph = new_ph
        return new_ph



def store_ph(new_ph):
    ph_buffer.append(new_ph)
    if len(ph_buffer) > BUFFER_SIZE:
        ph_buffer.pop(0)
    print(f"[SMOOTH DEBUG] Buffer: {['%.3f' % p for p in ph_buffer]}")



def get_smoothed_ph():
    if not ph_buffer:
        print("[SMOOTH DEBUG] Buffer is empty.")
        return None
    avg = sum(ph_buffer) / len(ph_buffer)
    print(f"[SMOOTH DEBUG] Smoothed pH: {avg:.3f}")
    return avg



# ---------------------------
# Function: Set Time via NTP
# ---------------------------
def set_time():
    ntp_servers = ['time.google.com', 'time.windows.com', 'asia.pool.ntp.org']
    for server in ntp_servers:
        try:
            wdt.feed()
            ntptime.host = server
            ntptime.settime()
            print(f"[TIME]: Time synchronized successfully using {server}.")
            timezone_offset = 8 * 60 * 60  # UTC+8
            rtc = machine.RTC()
            adjusted_time = time.mktime(time.localtime()) + timezone_offset
            tm = time.localtime(adjusted_time)
            rtc.datetime((tm[0], tm[1], tm[2], tm[6] + 1, tm[3], tm[4], tm[5], 0))
            return
        except Exception as e:
            print(f"Failed to set time using {server}: {e}")
    print("All NTP servers failed. Could not set time.")

# ---------------------------
# Function: Ensure WiFi is Connected
# ---------------------------
def ensure_wifi_connected():
    if not wlan.isconnected():
        maybe_collect_gc()
        wdt.feed()  # Feed the watchdog before starting

        print("WiFi lost! Attempting reconnection...")
        wlan.active(True)
        wlan.connect("your_SSID", "your_PASSWORD")
        timeout = time.time() + 30  # Wait up to 30 seconds
        retry_interval = 1  # Check every 1 second
        
        while not wlan.isconnected() and time.time() < timeout:
            wdt.feed()  # Feed the watchdog inside the loop
            time.sleep(retry_interval)

        print("Free memory after trying ti reconnecct line: 198", gc.mem_free()) #pylint: disable=no-member line: 90
        
        if wlan.isconnected():
            print("Reconnected to WiFi:", wlan.ifconfig())
        else:
            print("Failed to reconnect to WiFi. Backing off for 10 seconds.")
            time.sleep(10)  # Back-off delay before retrying later


def monitor_memory():
    # Force a garbage collection
    gc.collect()
    free = gc.mem_free() #pylint: disable=no-member
    alloc = gc.mem_alloc() #pylint: disable=no-member
    total = free + alloc
    print("Memory usage -> Allocated: {} bytes, Free: {} bytes, Total: {} bytes".format(alloc, free, total))
    
    try:
        block_size = 1024 * 10  # try to allocate 10KB
        buf = bytearray(block_size)
        print("[MEMORY]: Successfully allocated a contiguous block of {} bytes.".format(block_size))
        del buf  # Free it after testing
    except MemoryError:
        print("Failed to allocate a contiguous block of {} bytes - possible fragmentation.".format(block_size))

# ---------------------------
# Firebase Upload Function (returns status code)
# ---------------------------
def upload_data(path, value, method='PUT', retries=5):  # Reduced retries to save memory
    global token, token_refresh_time
    current_time_local = time.time()
    maybe_collect_gc()
    wdt.feed()  # Feed the watchdog at the start

    # Refresh token if nearing expiry
    if token and (current_time_local - token_refresh_time) >= TOKEN_EXPIRY_TIME:
        print("Token might be expired, refreshing...")
        token = firebase_login()
        if token:
            token_refresh_time = current_time_local
            maybe_collect_gc()
            
    if token is None:
        print("No Firebase token. Attempting to login...")
        token = firebase_login()
        if token:
            token_refresh_time = current_time_local
            print("Free after firebase login: 230", gc.mem_free()) #pylint: disable=no-member
            maybe_collect_gc()
        else:
            print("Login failed. Skipping upload.")
            return None
    
    # Pre-construct URL and headers
    url = f"{FIREBASE_URL}{path}.json?auth={token}"
    headers = {"content-type": "application/json; charset=UTF-8"}

    # Optimize value construction: allow different data types
    if not isinstance(value, dict):
        ts = construct_timestamp()
        value = {"value": value, "timestamp": ts}
    elif "timestamp" not in value:
        value["timestamp"] = construct_timestamp()

    # Exponential back-off setup
    backoff = 1
    for attempt in range(retries):
        
        response = None
        try:
            print(f"[ANNOUNCE]: Attempting to upload data (attempt {attempt+1})...")
            # monitor_memory()
            wdt.feed()
            # Use explicit timeout to avoid hanging operations
            if method == 'PUT':
                response = urequests.put(url, json=value, headers=headers, timeout=90)
            else:
                response = urequests.post(url, json=value, headers=headers, timeout=90)
            
            
            maybe_collect_gc()
            print("Free memory after upload data: line: 258", gc.mem_free()) #pylint: disable=no-member line: 258

            status = response.status_code
            maybe_collect_gc()
            if status == 200:
                print(f"[UPLOAD SUCCESS]: Successfully uploaded to {path}")
                return status
            elif status == 401:
                print("Token expired during request, refreshing...")
                response.close()
                token = firebase_login()
                if token:
                    token_refresh_time = time.time()
                    url = f"{FIREBASE_URL}{path}.json?auth={token}"
                    continue
                else:
                    print("Token refresh failed")
                    return None
            else:
                print(f"Failed to upload to {path}, status code: {status}")
                return None
        except Exception as e:
            wdt.feed()
            print(f"Error uploading to {path} (attempt {attempt + 1}): {e}")
            maybe_collect_gc()
            print("error uploading data line: 290 ", gc.mem_free()) #pylint: disable=no-member
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            if attempt == retries - 1:
                return None
        finally:
            if response:
                response.close()
    return None


# ---------------------------
# Firebase Login Function
# ---------------------------
def firebase_login():
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={API_KEY}"
    payload = {"email": EMAIL, "password": PASSWORD, "returnSecureToken": True}
    headers = {"content-type": "application/json; charset=UTF-8"}
    for attempt in range(3):
        response = None
        try:
            print("[ANNOUNCE]: Neuro Dog is fed before logging in")
            wdt.feed()  # Feed the watchdog at the start of each attempt
            gc.collect()
            print("[MEMORY] Before Firebase login:", gc.mem_free()) # pylint: disable=no-member

            maybe_collect_gc()
            response = urequests.post(url, json=payload, headers=headers)
            if response.status_code == 200:
                token = response.json().get('idToken')
                return token
            else:
                print(f"Firebase login failed with status code: {response.status_code}")
                print(f"Response: {response.text}")
        except Exception as e:
            print(f"Firebase login attempt {attempt + 1} failed: {e}")
            wdt.feed()
            gc.collect()
            print("[MEMORY] after firebase login fail memory:", gc.mem_free()) # pylint: disable=no-member
            time.sleep(5)
        finally:
            if response:
                response.close()
    return None

# ---------------------------
# WiFi and Time Setup
# ---------------------------
wlan = network.WLAN(network.STA_IF)
if wlan.isconnected():
    print("Connected to WiFi:", wlan.ifconfig())
    set_time()
    time.sleep(1)  # Small delay before proceeding
else:
    print("WiFi not connected. Cannot set time.")

# ---------------------------
# Data Restoration from Firebase
# ---------------------------
def restore_daily_data():
    global highest_temp, lowest_temp, temp_sum, temp_count, above_threshold_count, below_threshold_count, threshold_counts
    global highest_ph, lowest_ph, ph_sum, ph_count, ph_above_threshold_count, ph_below_threshold_count, ph_threshold_counts
    maybe_collect_gc()
    current_time = time.localtime()
    today_date = "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])
    path = f"/dailyData/{today_date}"
    url = f"{FIREBASE_URL}{path}.json?auth={token}"
    response = None
    try:
        wdt.feed()  # Feed the watchdog at the start of each attempt
        response = urequests.get(url)
        maybe_collect_gc()
        if response.status_code == 200:
            data = response.json()
            if data:
                # Restore temperature data
                temp_data = data.get("temperature", {})
                if "highest" in temp_data:
                    highest_temp = temp_data["highest"].get("value", highest_temp)
                if "lowest" in temp_data:
                    lowest_temp = temp_data["lowest"].get("value", lowest_temp)
                if "aboveThresholdCount" in temp_data:
                    above_threshold_count = temp_data["aboveThresholdCount"].get("value", above_threshold_count)
                if "belowThresholdCount" in temp_data:
                    below_threshold_count = temp_data["belowThresholdCount"].get("value", below_threshold_count)
                if "timeBasedCounts" in temp_data:
                    threshold_counts = temp_data["timeBasedCounts"]
                # Restore cumulative data for temperature
                if "temp_sum" in temp_data:
                    temp_sum = temp_data["temp_sum"].get("value", temp_sum)
                if "temp_count" in temp_data:
                    temp_count = temp_data["temp_count"].get("value", temp_count)
                
                # Restore pH data
                ph_data = data.get("pH", {})
                if "highest" in ph_data:
                    highest_ph = ph_data["highest"].get("value", highest_ph)
                if "lowest" in ph_data:
                    lowest_ph = ph_data["lowest"].get("value", lowest_ph)
                if "aboveThresholdCount" in ph_data:
                    ph_above_threshold_count = ph_data["aboveThresholdCount"].get("value", ph_above_threshold_count)
                if "belowThresholdCount" in ph_data:
                    ph_below_threshold_count = ph_data["belowThresholdCount"].get("value", ph_below_threshold_count)
                if "timeBasedCounts" in ph_data:
                    ph_threshold_counts = ph_data["timeBasedCounts"]
                # Restore cumulative data for pH
                if "ph_sum" in ph_data:
                    ph_sum = ph_data["ph_sum"].get("value", ph_sum)
                if "ph_count" in ph_data:
                    ph_count = ph_data["ph_count"].get("value", ph_count)

                print("Free memory after restore daily data line: 392:", gc.mem_free()) #pylint: disable=no-member
                print("Daily data restored for today:", today_date)
                
                # Print the restored data for debugging
                print("Restored Temperature Data:")
                print({
                    "highest_temp": highest_temp,
                    "lowest_temp": lowest_temp,
                    "temp_sum": temp_sum,
                    "temp_count": temp_count,
                    "above_threshold_count": above_threshold_count,
                    "below_threshold_count": below_threshold_count,
                    "threshold_counts": threshold_counts
                })
                print("Restored pH Data:")
                print({
                    "highest_ph": highest_ph,
                    "lowest_ph": lowest_ph,
                    "ph_sum": ph_sum,
                    "ph_count": ph_count,
                    "ph_above_threshold_count": ph_above_threshold_count,
                    "ph_below_threshold_count": ph_below_threshold_count,
                    "ph_threshold_counts": ph_threshold_counts
                })
            else:
                print("No daily data found for today:", today_date)
        else:
            print("Failed to retrieve daily data, status code:", response.status_code)
            wdt.feed()
            maybe_collect_gc()

    except Exception as e:
        print("Exception in restoring daily data:", e)
        wdt.feed()
    finally:
        if response:
            response.close()



def maybe_collect_gc():
    wdt.feed()  # Feed the watchdog before collecting
    if gc.mem_free() < 70000:  # pylint: disable=no-member
        gc.collect()
        print("[ANNOUNCE]: GC collected. Free memory now:", gc.mem_free())  # pylint: disable=no-member
        return True


def lower_maybe_collect_gc():
    wdt.feed()  # Feed the watchdog before collecting
    if gc.mem_free() < 60000:  # pylint: disable=no-member
        gc.collect()
        print("[ANNOUNCE]: GC collected. Free memory now:", gc.mem_free())  # pylint: disable=no-member
        return True

def set_servo_angle(angle):
    # Map 0-180 degrees to 1000-2000 microseconds pulse width
    wdt.feed()  # Feed the watchdog at the start of each attempt
    pulse_us = 1000 + (angle / 180.0) * 1000  # 1000µs for 0, 2000µs for 180
    duty = int((pulse_us / 20000) * 1023)
    print("[ANNOUNCE]: Setting servo angle:", angle, "Pulse:", pulse_us, "Duty:", duty)
    servo.duty(duty)

# ---------------------------
# Automated Feeding & Servo Setup
# ---------------------------
last_feeding_check = 0
FEEDING_CHECK_INTERVAL = 60 * 2  # every minute

servo_pin = machine.Pin(13)
# Create a PWM object at 50Hz (standard for servos)
servo = machine.PWM(servo_pin, freq=50)

# ---------------------------
# Firebase Listener for Servo Control
# ---------------------------

# Define feeding periods
FEEDING_PERIODS = {
    "morning": {
        "hour": 5,
    },
    "evening": {
        "hour": 18,
    }
}

# Add function to restore feeding status
def restore_feeding_status():
    try:
        current_date = "{:04d}-{:02d}-{:02d}".format(*time.localtime()[:3])
        url = f"{FIREBASE_URL}/feedingStatus/{current_date}.json?auth={token}"
        response = urequests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data:
                for period in FEEDING_PERIODS:
                    FEEDING_PERIODS[period]["completed"] = data.get(period, {}).get("completed", False)
            else:
                # Initialize new day
                for period in FEEDING_PERIODS:
                    FEEDING_PERIODS[period]["completed"] = False
        response.close()
    except Exception as e:
        print(f"Error restoring feeding status: {e}")
        # Default to False if can't restore
        for period in FEEDING_PERIODS:
            FEEDING_PERIODS[period]["completed"] = False

def update_feeding_status(period, completed):
    try:
        current_date = "{:04d}-{:02d}-{:02d}".format(*time.localtime()[:3])
        data = {
            "completed": completed,
            "timestamp": construct_timestamp()
        }
        upload_data(f"/feedingStatus/{current_date}/{period}", data)
    except Exception as e:
        print(f"Error updating feeding status: {e}")

# Modify check_feeding_schedule
def check_feeding_schedule():
    current_hour = time.localtime()[3]
    
    # Reset at midnight
    if current_hour == 0:
        for period in FEEDING_PERIODS:
            FEEDING_PERIODS[period]["completed"] = False
            update_feeding_status(period, False)
    
    for period, config in FEEDING_PERIODS.items():
        if current_hour >= config["hour"] and not config.get("completed", False):
            print(f"[FEEDING DEBUG] Attempting {period} feeding...")
            if feed_prawn(is_automated=True):
                FEEDING_PERIODS[period]["completed"] = True
                update_feeding_status(period, True)
                print(f"[FEEDING]: Successfully completed {period} feeding")


def feed_prawn(is_automated=False):
    try:
        maybe_collect_gc()
        for _ in range(1):
            wdt.feed()
            set_servo_angle(0)
            time.sleep(2)
            set_servo_angle(180)
            time.sleep(2)
        set_servo_angle(0)
        time.sleep(2)  # Allow system to stabilize
        if is_automated:
            current_time = time.localtime()
            hour = current_time[3]
            period = "morning" if hour < 12 else "evening"
            message = f"Automated {period} feeding completed"
        else:
            message = "Manual feeding completed"
        send_notification(message, "servo_active", "servo")
        print("[FEEDING]: Free memory after feed prawn", gc.mem_free()) #pylint: disable=no-member
        return True  # Indicate success
    except Exception as e:
        print("Error in feed_prawn:", e)
        wdt.feed()  # Feed the watchdog before returning
        print("Free memory after feed prawn error", gc.mem_free()) #pylint: disable=no-member
        return False


def check_servo_control(max_retries=1):
    if token is None:
        return
    for attempt in range(max_retries):
        response = None
        lower_maybe_collect_gc()  # Collect memory before making network call
        try:
            url = f"{FIREBASE_URL}/servoControl.json?auth={token}"
            print(f"[FEEDING]: Getting command from URL (attempt {attempt+1})")
            wdt.feed()  # Feed watchdog
            maybe_collect_gc()  # Collect memory before making network call
            response = urequests.get(url, timeout=60)
            if response.status_code == 200:
               
                data = response.json()
                if data and isinstance(data, dict):
                    command = data.get('command')
                    if command == 'feed':
                        print("[FEEDING]: Command is 'feed'")
                        try:
                            success = feed_prawn()
                            if success:
                                wdt.feed()  # Feed watchdog before deletion
                                print("[FEEDING]: Deleting command node")
                                maybe_collect_gc()
                                resp_del = urequests.delete(url, timeout=60)
                                if resp_del:
                                    print("[FEEDING]: Free memory after deleting servoControl Node:", gc.mem_free())#pylint: disable=no-member
                                    resp_del.close()
                            else:
                                print("[FEEDING]: Feed action failed, command node not deleted.")
                                maybe_collect_gc()
                                wdt.feed()
                        except Exception as e:
                            print("[FEEDING]: Error during feeding or deletion:", e)
                            print("[FEEDING]: Free memory:", gc.mem_free()) #pylint: disable=no-member
                        # If command 'feed' processed, break out of the retry loop
                        break
                    else:
                        print("[FEEDING]: Command is not 'feed'")
                        break  # No feed command; exit loop.
                else:
                    print("[FEEDING]: No valid data received.")
            else:
                print("[FEEDING]: Received non-200 response:", response.status_code)
        except Exception as e:
            print(f"[FEEDING]: Error checking servo control on attempt {attempt+1}: {e}")
            print("[FEEDING]: Free memory:", gc.mem_free())#pylint: disable=no-member
        finally:
            if response:
                response.close()
        # Wait a bit before retrying
        time.sleep(1)


# ---------------------------
# Now that restore_daily_data is defined, we check WiFi and call it
# ---------------------------
if wlan.isconnected():
    token = firebase_login()
    if token:
        set_time()
        print("Current device time:", construct_timestamp())
        restore_daily_data()  # Now defined above
        restore_feeding_status()
        print("initializing servo to 0")
        set_servo_angle(0)
        time.sleep(2)
    else:
        print("Firebase login failed.")
else:
    print("WiFi not connected. Cannot log in to Firebase.")
    token = None

# ---------------------------
# Notification Function
# ---------------------------
# def send_notification(message, status, component):

#     current_time = time.localtime()
#     date_str = "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])
#     time_str = "{:02d}:{:02d}:{:02d}".format(current_time[3], current_time[4], current_time[5])
#     timestamp_str = construct_timestamp()
    
#     notification_data = {
#         "message": message,
#         "status": status,
#         "timestamp": timestamp_str,
#         "component": component,
#         "type": status.split('_')[0] if '_' in status else status
#     }

#     wdt.feed()  # Feed the watchdog at the start of each attempt
#     maybe_collect_gc()
#     comp_status = upload_data(f'/componentNotifications/{component}/{date_str}/{time_str}', notification_data, method='PUT')
#     dash_status = upload_data(f'/dashboardNotifications/{date_str}/{time_str}', notification_data, method='PUT')
    
#     if comp_status == 200 and dash_status == 200:
#         print(f'[NOTIFICATION]: Notification sent successfully: {message}')
#     else:
#         print(f'Failed to send notification: Component: {comp_status if comp_status else "No response"}, '
#               f'Dashboard: {dash_status if dash_status else "No response"}')
#         wdt.feed()
#         maybe_collect_gc()

def send_notification(message, status, component):
    current_time = time.localtime()
    date_str = "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])
    time_str = "{:02d}:{:02d}:{:02d}".format(current_time[3], current_time[4], current_time[5])
    timestamp_str = construct_timestamp()
    
    notification_data = {
        "message": message,
        "status": status,
        "timestamp": timestamp_str,
        "component": component,
        "type": status.split('_')[0] if '_' in status else status
    }

    # Add these debug prints
    print(f"[NOTIFICATION DEBUG] Component: {component}")
    print(f"[NOTIFICATION DEBUG] Path: /componentNotifications/{component}/{date_str}/{time_str}")
    print(f"[NOTIFICATION DEBUG] Data: {notification_data}")
    
    comp_status = upload_data(f'/componentNotifications/{component}/{date_str}/{time_str}', notification_data, method='PUT')
    dash_status = upload_data(f'/dashboardNotifications/{date_str}/{time_str}', notification_data, method='PUT')
    
    print(f"[NOTIFICATION DEBUG] Component status: {comp_status}")
    print(f"[NOTIFICATION DEBUG] Dashboard status: {dash_status}")

# ---------------------------
# Sensor Reading Functions
# ---------------------------
# DS18B20 Temperature Sensor Setup
dat = Pin(25)
ds = ds18x20.DS18X20(onewire.OneWire(dat))
roms = ds.scan()
print('Found DS devices:', roms)

def read_temperature_sensor(max_retries=3):
    wdt.feed()  # Feed the watchdog at the start of each attempt
    for attempt in range(max_retries):
        try:
            wdt.feed()
            ds.convert_temp()
            time.sleep(1)  # Allow conversion
            temp = ds.read_temp(roms[0])
            if temp is not None:
                return temp
        except onewire.OneWireError as e:
            print("Attempt {}: Error reading DS sensor: {}".format(attempt + 1, e))

            wdt.feed()
            maybe_collect_gc()
            print("line: 468 ", gc.mem_free()) #pylint: disable=no-member

            time.sleep(1)
    print("Failed to read DS sensor after {} attempts.".format(max_retries))
    return None


def update_threshold_counter(component, counter_name, value):
    wdt.feed()  # Feed the watchdog at the start of each attempt
    current_time = time.localtime()
    today_date = "{:04d}-{:02d}-{:02d}".format(
        current_time[0], current_time[1], current_time[2]
    )
    # Build the path to the specific counter field
    # For example: /dailyData/2025-03-09/temperature/aboveThresholdCount
    path = f"/dailyData/{today_date}/{component}/{counter_name}"
    payload = {
        "value": value,
        "timestamp": construct_timestamp()
    }
    upload_data(path, payload, method='PUT')
    print("Free memory after update threshold counter:", gc.mem_free()) #pylint: disable=no-member line: 475

def update_time_based_counts(component, counts):
    wdt.feed()  # Feed the watchdog at the start of each attempt
    current_time = time.localtime()
    today_date = "{:04d}-{:02d}-{:02d}".format(
        current_time[0], current_time[1], current_time[2]
    )
    path = f"/dailyData/{today_date}/{component}/timeBasedCounts"
    payload = counts.copy()  # Create a copy of the counts dictionary
    payload["timestamp"] = construct_timestamp()  # Add the timestamp
    upload_data(path, payload, method='PUT')
    print("Free memory after update time based count data:", gc.mem_free()) #pylint: disable=no-member line: 487

def update_field(component, field_name, value):
    wdt.feed()  # Feed the watchdog at the start of each attempt
    current_time = time.localtime()
    today_date = "{:04d}-{:02d}-{:02d}".format(
        current_time[0], current_time[1], current_time[2]
    )
    path = f"/dailyData/{today_date}/{component}/{field_name}"
    payload = {
        "value": value,
        "timestamp": construct_timestamp()
    }
    upload_data(path, payload, method='PUT')
    print("Free memory after update field:", gc.mem_free()) #pylint: disable=no-member line: 501



# ---------------------------
# Alert Handlers for Temperature & pH
# ---------------------------
def get_time_period(hour):
    if 6 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 24:
        return "evening"
    else:
        return "night"


last_threshold_update = {
    "temperature": 0,
    "pH": 0
}

COOLDOWN_PERIOD = 60 * 5  # 5 minutes


def handle_alert_state_temp(temp, current_hour):
    global current_alert_state_temp, above_threshold_count, below_threshold_count, threshold_counts
    
    # Convert to whole number for comparison
    temp_whole = int(temp)
    threshold_high_whole = int(THRESHOLD_TEMP_HIGH)
    threshold_low_whole = int(THRESHOLD_TEMP_LOW)
    
    # Determine new state using whole numbers
    new_state = 'above' if temp_whole > threshold_high_whole else 'below' if temp_whole < threshold_low_whole else 'normal'
    
    current_time_local = time.time()
    time_period = get_time_period(current_hour)

    # # Debug prints
    # print(f"[TEMP DEBUG] Raw temp: {temp:.1f}C, Whole number: {temp_whole}C")
    # print(f"[TEMP DEBUG] Current state: {current_alert_state_temp}, New state: {new_state}")
    # print(f"[TEMP DEBUG] Thresholds - High: {threshold_high_whole}C, Low: {threshold_low_whole}C")

    last_notif = get_last_notification("temperature")
    if last_notif is None:
        last_notif = 0
    
    # print(f"[TEMP DEBUG] Last notification time: {last_notif}")
    # print(f"[TEMP DEBUG] Current time: {current_time_local}")
    # print(f"[TEMP DEBUG] Time since last notification: {current_time_local - last_notif} seconds")
    # # print(f"[TEMP DEBUG] Cooldown period: {COOLDOWN_PERIOD} seconds")

    if new_state != current_alert_state_temp and (current_time_local - last_notif) >= COOLDOWN_PERIOD:
        print(f"[TEMP DEBUG] State change detected! Sending notification for: {new_state}")
        if new_state == 'above':
            above_threshold_count += 1
            threshold_counts[time_period]["above"] += 1
            # Remove degree symbol from message
            message = f"Temperature went above threshold: {temp:.1f}C (Limit: {threshold_high_whole}C)"
            print(f"[TEMP DEBUG] Sending above threshold notification. Count: {above_threshold_count}")
            send_notification(message, "temp_above", "temperature")
        elif new_state == 'below':
            below_threshold_count += 1
            threshold_counts[time_period]["below"] += 1
            # Remove degree symbol from message
            message = f"Temperature went below threshold: {temp:.1f}C (Limit: {threshold_low_whole}C)"
            print(f"[TEMP DEBUG] Sending below threshold notification. Count: {below_threshold_count}")
            send_notification(message, "temp_below", "temperature")
        else:
            # Remove degree symbol from message
            message = f"Temperature returned to normal: {temp:.1f}C"
            print("[TEMP DEBUG] Temperature normalized, updating current alert")
            upload_data('/currentTemperature', {'temperature': temp, 'status': 'temp_normal', 'message': message})
        
        set_last_notification("temperature", current_time_local)
        print(f"[TEMP DEBUG] Last notification time updated to: {current_time_local}")
    else:
        print("[TEMP DEBUG] No notification sent - Either state unchanged or in cooldown period")
    
    print(f"[TEMP DEBUG] Threshold counts for {time_period}: {threshold_counts[time_period]}")
    current_alert_state_temp = new_state

def handle_alert_state_ph(ph, current_hour):
    global current_alert_state_ph, ph_above_threshold_count, ph_below_threshold_count, ph_threshold_counts
    # Round to 1 decimal place for comparison
    CHANGE_THRESHOLD = 0.05  # Minimum change to consider (e.g., 7.10 to 7.15)
    ph_rounded = round(ph, 2)  # Keep 2 decimal places instead of 1
    
    EPSILON = 0.01  # small buffer for floating point quirks

    if ph_rounded > THRESHOLD_PH_HIGH + EPSILON:
        new_state = 'above'
    elif ph_rounded < THRESHOLD_PH_LOW - EPSILON:
        new_state = 'below'
    else:
        new_state = 'normal'


    current_time_local = time.time()
    time_period = get_time_period(current_hour)

    last_notif = get_last_notification("pH")
    if last_notif is None:
        last_notif = 0

    if new_state != current_alert_state_ph and (current_time_local - last_notif) >= COOLDOWN_PERIOD:
        if new_state == 'above':
            ph_above_threshold_count += 1
            ph_threshold_counts[time_period]["above"] += 1
            message = f"pH went above threshold: {ph:.1f} (Limit: {THRESHOLD_PH_HIGH})"
            send_notification(message, "ph_above", "pH")
        elif new_state == 'below':
            ph_below_threshold_count += 1
            ph_threshold_counts[time_period]["below"] += 1
            message = f"pH went below threshold: {ph:.1f} (Limit: {THRESHOLD_PH_LOW})"
            send_notification(message, "ph_below", "pH")
        else:
            message = f"pH returned to normal: {ph:.1f}"
            upload_data('/currentPH', {"pH": ph, "message": message})
        
        set_last_notification("pH", current_time_local)
    
    current_alert_state_ph = new_state

def push_time_based_counts():
    current_time_struct = time.localtime()
    maybe_collect_gc()
    update_time_based_counts("temperature", threshold_counts)
    update_time_based_counts("pH", ph_threshold_counts)



# ---------------------------
# Daily Data Update & Reset Functions
# ---------------------------
def update_daily_data():
    maybe_collect_gc()
    wdt.feed()  # Feed the watchdog
    current_time = time.localtime()
    date_str = "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])
    base_path = f"/dailyData/{date_str}"
    
    # Build temperature data payload
    temp_stats = {
        "highest": {"value": highest_temp, "timestamp": construct_timestamp()},
        "lowest": {"value": lowest_temp, "timestamp": construct_timestamp()},
        "aboveThresholdCount": {"value": above_threshold_count, "timestamp": construct_timestamp()},
        "belowThresholdCount": {"value": below_threshold_count, "timestamp": construct_timestamp()},
        "timeBasedCounts": {
            "morning": threshold_counts["morning"],
            "afternoon": threshold_counts["afternoon"],
            "evening": threshold_counts["evening"],
            "night": threshold_counts["night"],
            "timestamp": construct_timestamp()
        },
        "temp_sum": {"value": temp_sum, "timestamp": construct_timestamp()},
        "temp_count": {"value": temp_count, "timestamp": construct_timestamp()}
    }
    if temp_count > 0:
        average_temp = temp_sum / temp_count
        temp_stats["average"] = {"value": average_temp, "timestamp": construct_timestamp()}

    # Build pH data payload
    ph_stats = {
        "highest": {"value": highest_ph, "timestamp": construct_timestamp()},
        "lowest": {"value": lowest_ph, "timestamp": construct_timestamp()},
        "aboveThresholdCount": {"value": ph_above_threshold_count, "timestamp": construct_timestamp()},
        "belowThresholdCount": {"value": ph_below_threshold_count, "timestamp": construct_timestamp()},
        "timeBasedCounts": {
            "morning": ph_threshold_counts["morning"],
            "afternoon": ph_threshold_counts["afternoon"],
            "evening": ph_threshold_counts["evening"],
            "night": ph_threshold_counts["night"],  
            "timestamp": construct_timestamp()
        },
        "ph_sum": {"value": ph_sum, "timestamp": construct_timestamp()},
        "ph_count": {"value": ph_count, "timestamp": construct_timestamp()}
    }
    if ph_count > 0:
        average_ph = ph_sum / ph_count
        ph_stats["average"] = {"value": average_ph, "timestamp": construct_timestamp()}
    
    # Upload temperature and pH data separately
    wdt.feed()  # Feed the watchdog before uploading
    maybe_collect_gc()
    upload_data(f"{base_path}/temperature", temp_stats, method='PUT')

    time.sleep(3)  # Small delay before uploading pH data
    wdt.feed()  # Feed the watchdog before uploading
    maybe_collect_gc()
    upload_data(f"{base_path}/pH", ph_stats, method='PUT')
    
    print("Free memory after update daily data: line: 700", gc.mem_free()) #pylint: disable=no-member line: 700


def save_daily_data():
    update_daily_data()

def reset_daily_data():
    wdt.feed()  # Feed the watchdog at the start of each attempt
    maybe_collect_gc()
    global highest_temp, lowest_temp, temp_sum, temp_count, above_threshold_count, below_threshold_count, threshold_counts
    global highest_ph, lowest_ph, ph_sum, ph_count, ph_above_threshold_count, ph_below_threshold_count, ph_threshold_counts
    highest_temp = -100.0
    lowest_temp = 100.0
    temp_sum = 0.0
    temp_count = 0
    above_threshold_count = 0
    below_threshold_count = 0
    threshold_counts = {
        "morning": {"above": 0, "below": 0},
        "afternoon": {"above": 0, "below": 0},
        "evening": {"above": 0, "below": 0},
        "night": {"above": 0, "below": 0},
    }
    highest_ph = 0.0
    lowest_ph = 14.0
    ph_sum = 0.0
    ph_count = 0
    ph_above_threshold_count = 0
    ph_below_threshold_count = 0
    ph_threshold_counts = {
        "morning": {"above": 0, "below": 0},
        "afternoon": {"above": 0, "below": 0},
        "evening": {"above": 0, "below": 0},
        "night": {"above": 0, "below": 0},
    }

# ---------------------------
# Heating Rod Control Function
# ---------------------------

def control_heating_rod(temp):
    if temp is not None:
        if temp < 26:
            relay.value(1)  # Set pin high to activate relay
        else:
            relay.value(0)  # Set pin low to deactivate relay




def push_overtime_readings(temp, ph):
    wdt.feed()  # Feed the watchdog at the start of each attempt

    current_ts = construct_timestamp()
    today_date = "{:04d}-{:02d}-{:02d}".format(*time.localtime()[:3])
    
    temp_path = f"/temperatureOverTime/{today_date}"
    ph_path = f"/pHOverTime/{today_date}"
    
    print("[DEBUG]: Pushing overtime readings")
    
    # Push the current reading with timestamp into the overtime nodes.
    maybe_collect_gc()
    print("[DEBUG]: Temp Path:", temp_path, "Data:", {"timestamp": current_ts, "temperature": temp})
    temp_status = upload_data(temp_path, {"timestamp": current_ts, "temperature": temp}, method="POST")
    time.sleep(2)
    print("[DEBUG]: pH Path:", ph_path, "Data:", {"timestamp": current_ts, "pH": ph})
    ph_status = upload_data(ph_path, {"timestamp": current_ts, "pH": ph}, method="POST")
    print("[DEBUG]: Temp upload status:", temp_status, "| pH upload status:", ph_status)

def staggered_summary_upload(temp, ph):
    """Performs summary uploads in stages to prevent memory spikes"""
    try:
        # Stage 1: Overtime readings
        wdt.feed()
        maybe_collect_gc()
        print("[SUMMARY] Stage 1: Pushing overtime readings")
        push_overtime_readings(temp, ph)
        time.sleep(2)
        
        # Stage 2: Threshold counts
        wdt.feed()
        maybe_collect_gc()
        print("[SUMMARY] Stage 2: Pushing threshold counts")
        push_time_based_counts()
        time.sleep(2)
        
        # Stage 3: Daily stats
        wdt.feed()
        maybe_collect_gc()
        print("[SUMMARY] Stage 3: Saving daily data")
        save_daily_data()
        
        return True
    except Exception as e:
        print(f"[SUMMARY ERROR]: {e}")
        return False


# ---------------------------
# Main Loop Configurationf
# ---------------------------
# TEST_MODE = False         # Set True for testing mode
start_time = time.time()
REAL_TIME_UPLOAD_INTERVAL = 60   # 2 minutes
SUMMARY_UPLOAD_INTERVAL = 60 * 10     # 10 minutes
last_temp_upload_time = time.time()
last_summary_upload_time = time.time()
last_servo_check_time = time.time()   
last_feeding_check = time.time()
SERVO_CHECK_INTERVAL = 10  # every 10 seconds
last_date = (time.localtime()[0], time.localtime()[1], time.localtime()[2])
summary_upload_count = 0  # global variable
MAX_SUMMARY_UPLOADS = 5  # Reset after 5 successful summary uploads


while True:
    # # 1. Basic sensor readings
    # if roms:
    #     maybe_collect_gc()
    #     temp = read_temperature_sensor()
    #     if temp is not None:
    #         print(f"Raw temperature: {temp:.1f}C")
    #         control_heating_rod(temp)
            
    # else:
    #     print("No DS sensor detected.")
    #     temp = None 

    # # 2. pH Reading with compensation
    # maybe_collect_gc()
    # ph_raw = read_ph()
    # compensated_ph = None
   
    # if ph_raw is not None:
    #     ph_filtered = filter_spike(ph_raw)
    #     store_ph(ph_filtered)
    #     smoothed_ph = get_smoothed_ph()

    #     if smoothed_ph is not None and temp is not None:
    #          ph = compensate_ph_linear(smoothed_ph, temp) 
    #          print("Temperature:", temp, "| pH:", ph)
             
    # else:
    #     print("[WARNING] Raw pH reading failed.")

    # # for calibration
    # # read_ph_raw_voltage()
    # check_servo_control()
    
    # time.sleep(2)  # Short delay between readings


    # ===============[PRODUCTION COLDE]===================
    # Feed the watchdog (reset the timer)
    wdt.feed()
    print("[ANNOUNCE]: Neuro Dog is fed at the start of loop")
    elapsed_time = time.time() - last_summary_upload_time

    # Perform garbage collection
    maybe_collect_gc()
    # Ensure WiFi is connected

    print("[WIFI]: Checked WiFi connection")
    ensure_wifi_connected()
  


    # Read sensors once and use the readings
    if roms:
        maybe_collect_gc()
        temp = read_temperature_sensor()
        if temp is not None:
            control_heating_rod(temp)
    else:
        print("No DS sensor detected.")
        temp = None

    maybe_collect_gc()
    current_time_val = time.time()
    raw_ph = read_ph()
    if raw_ph is not None:
        filtered_ph = filter_spike(raw_ph)
        store_ph(filtered_ph)
        smoothed_ph = get_smoothed_ph()
        
        if smoothed_ph is not None and temp is not None:
            ph = compensate_ph_linear(smoothed_ph, temp) 
            #compensated ph final value before
            #this ph is smoothed, filtered, and compensated
            print(f"[PH COMPENSATION] Raw: {smoothed_ph}, Temp: {temp}°C, Compensated: {ph}")
        else:
            print("[WARNING] Temp or pH invalid for compensation")
        
    else:
        print("Warning: pH reading failed. Using last known pH.")
        ph = None  # or you can keep previous `ph` value

    print("Temperature:", temp, "| pH:", ph)


    if current_time_val - last_feeding_check >= FEEDING_CHECK_INTERVAL:
        maybe_collect_gc()
        print("[FEEDING]: Checking automated feeding schedule")
        check_feeding_schedule()  # Add this line
        last_feeding_check = current_time_val

    if current_time_val - last_servo_check_time >= SERVO_CHECK_INTERVAL:
        print("[FEEDING]: Free memory before checking servo control line:933", gc.mem_free()) #pylint: disable=no-member 
        maybe_collect_gc()
        check_servo_control()
        last_servo_check_time = current_time_val

    try:
        maybe_collect_gc()
        current_time_struct = time.localtime()
        current_hour = current_time_struct[3]
        
        if ph is not None:
            ph_sum += ph
            ph_count += 1
        else:
            print("Warning: pH reading is None")

        if temp is not None:
            temp_sum += temp
            temp_count += 1
        else:
            print("Warning: Temperature reading is None")

        if temp > highest_temp:
            highest_temp = temp
            maybe_collect_gc()
            update_field("temperature", "highest", highest_temp)
        if temp < lowest_temp:
            lowest_temp = temp
            maybe_collect_gc()
            update_field("temperature", "lowest", lowest_temp)


        if ph > highest_ph:
            highest_ph = ph
            maybe_collect_gc()
            update_field("pH", "highest", highest_ph)
        if ph < lowest_ph:
            lowest_ph = ph
            maybe_collect_gc()
            update_field("pH", "lowest", lowest_ph)


        maybe_collect_gc()
        handle_alert_state_temp(temp, current_hour)
        time.sleep(2)
        handle_alert_state_ph(ph, current_hour)
        
        if temp_count == 1:
            maybe_collect_gc()
            push_time_based_counts()  # Push the current threshold counts
            save_daily_data()
        
        current_date = (current_time_struct[0], current_time_struct[1], current_time_struct[2])
        if current_date != last_date:
            wdt.feed()
            maybe_collect_gc()
            push_time_based_counts()  # Push the current threshold counts
            save_daily_data()
            maybe_collect_gc()
            reset_daily_data()
            push_overtime_readings(temp, ph)
            last_date = current_date
        
        if time.time() - last_temp_upload_time >= REAL_TIME_UPLOAD_INTERVAL:
            wdt.feed()
            maybe_collect_gc()
            print("[Realtime]: Uploading current ph and temp")
            upload_data('/currentTemperature', {"temperature": temp})
            upload_data('/currentPH', {"pH": ph})
            last_temp_upload_time = time.time()
            print("[FREE MEMORY]: Free memory in the after uploading current ph and temp line: 945", gc.mem_free()) #pylint: disable=no-member 
            
        # In the main loop, modify the summary upload section:
        if time.time() - last_summary_upload_time >= SUMMARY_UPLOAD_INTERVAL:
            maybe_collect_gc()
            wdt.feed()
            print("[SUMMARY]: Starting staggered upload")
            if staggered_summary_upload(temp, ph):
                last_summary_upload_time = time.time()
                summary_upload_count += 1
                print("===============================================")
                print(f"[SUMMARY]: Upload {summary_upload_count}/{MAX_SUMMARY_UPLOADS} completed")
                print("===============================================")
                
                # Controlled reset after MAX_SUMMARY_UPLOADS
                if summary_upload_count >= MAX_SUMMARY_UPLOADS:
                    print("[SYSTEM]: Performing controlled reset after successful uploads")
                    time.sleep(2)  # Allow time for message to be printed
                    machine.reset()  # Perform soft reset
            else:
                print("[SUMMARY]: Upload failed")

        print("Temperature:", temp, "| pH:", ph)
        # monitor_memory()
        time.sleep(2)
        minutes_elapsed = int(elapsed_time // 60)
        print(f"[SUMMARY]: {minutes_elapsed} minutes have elapsed.")
        # Replace the single 30-second sleep with multiple shorter sleeps
        for _ in range(6):  # 6 iterations of 5 seconds each = 30 seconds total
            wdt.feed()
            time.sleep(5)
    except Exception as e:
        print(f"An error occurred: {e}")
        time.sleep(15)