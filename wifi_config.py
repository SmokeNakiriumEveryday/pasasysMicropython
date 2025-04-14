# wifi_config.py
# pylint: disable=import-error
import network
import ujson
import time
import socket
import ure
import machine  # For resetting the device and using the watchdog
import os

# Ensure the relay is off
relay = machine.Pin(15, machine.Pin.OUT, value=0)
relay.value(0)

print(os.listdir())
print(os.statvfs("/"))  # pylint: disable=no-member

CONFIG_FILE = "wifi_config.json"

# --- LED Indicator Setup ---
# Change these GPIO numbers as needed for your wiring.
YELLOW_LED_PIN = 12    # Example GPIO for yellow LED
GREEN_LED_PIN = 14     # Example GPIO for green LED

yellow_led = machine.Pin(YELLOW_LED_PIN, machine.Pin.OUT)
green_led = machine.Pin(GREEN_LED_PIN, machine.Pin.OUT)

# Make sure LEDs are off initially.
yellow_led.value(0)
green_led.value(0)

# --- Added Constants and Hardware Watchdog ---
AP_TIMEOUT = 600  # 10 minutes timeout for AP mode (in seconds)
wdt = machine.WDT(timeout=45000)  # 45-second hardware watchdog

def load_wifi_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            config = ujson.load(f)
            return config.get("ssid"), config.get("password")
    except Exception as e:
        print("No WiFi config found:", e)
        return None, None

def save_wifi_config(ssid, password):
    config = {"ssid": ssid, "password": password}
    with open(CONFIG_FILE, "w") as f:
        ujson.dump(config, f)
    print("WiFi credentials saved.")

def scan_networks(wlan):
    """Scans for available WiFi networks and prints found SSIDs."""
    print("Scanning for available networks...")
    nets = wlan.scan()
    available_ssids = []
    for net in nets:
        try:
            net_ssid = net[0].decode('utf-8')
        except Exception:
            net_ssid = net[0]
        print("Found network:", net_ssid)
        available_ssids.append(net_ssid)
    return available_ssids

def connect_to_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    # Scan and verify the desired SSID is visible
    available_ssids = scan_networks(wlan)
    if ssid not in available_ssids:
        print("SSID '{}' not found in scan results. Check hotspot settings.".format(ssid))
        yellow_led.value(0)
        green_led.value(0)
        return False

    # Connect to the given SSID with provided password.
    wlan.connect(ssid, password)
    print("Attempting to connect to WiFi '{}'...".format(ssid))
    
    # Turn on yellow LED while connecting; ensure green is off.
    yellow_led.value(1)
    green_led.value(0)
    
    # Wait up to 30 seconds (in 60 loops of 0.5s each) to connect.
    for i in range(60):
        wdt.feed()
        if wlan.isconnected():
            print("Connected to WiFi:", wlan.ifconfig())
            yellow_led.value(0)
            green_led.value(1)
            # Keep the green LED on for 5 seconds to indicate success.
            time.sleep(5)
            green_led.value(0)
            return True
        print("Connecting... ({}/{})".format(i + 1, 60))
        # Blink yellow LED during connection attempts.
        yellow_led.value(0)
        time.sleep(0.5)
        yellow_led.value(1)
        time.sleep(0.5)
        wdt.feed()  # Feed watchdog in loop to prevent resets.
    print("Failed to connect to WiFi.")
    yellow_led.value(0)
    green_led.value(0)
    return False

def start_access_point():
    """Starts the device in AP mode with configuration for 10 minutes."""
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    # Set up the AP with WPA/WPA2.
    ap.config(essid="PasaSys Wifi Config", authmode=network.AUTH_WPA_WPA2_PSK, password="pasasys123")
    print("Access Point started with SSID 'PasaSys Wifi Config'. AP config:", ap.ifconfig())
    
    # In AP mode, keep yellow LED on to show we're in configuration mode.
    yellow_led.value(1)
    green_led.value(0)
    
    return ap

def url_decode(s):
    """Simple URL decoding function that converts %XX escapes and plus signs to spaces."""
    res = ""
    i = 0
    while i < len(s):
        c = s[i]
        if c == '+':
            res += ' '
            i += 1
        elif c == '%' and i + 2 < len(s):
            try:
                hex_val = s[i+1:i+3]
                res += chr(int(hex_val, 16))
                i += 3
            except Exception as e:
                res += c
                i += 1
        else:
            res += c
            i += 1
    return res

def parse_post_data(body):
    params = {}
    pairs = body.split("&")
    for pair in pairs:
        try:
            key, value = pair.split("=")
            key = url_decode(key)
            value = url_decode(value)
            params[key] = value
        except Exception as e:
            print("Error parsing parameter:", e)
    return params

def send_response(client, response):
    try:
        client.send(response)
    except Exception as e:
        print("Error sending response:", e)
    finally:
        client.close()

def start_config_server():
    """
    Starts a simple HTTP server that serves an HTML form for entering WiFi credentials.
    When the form is submitted, credentials are saved and the device will reboot.
    The server also checks for the AP mode timeout and triggers a reboot if it expires.
    """
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.bind(addr)
    s.listen(1)
    print("Configuration server listening on", addr)
    
    start_time = time.time()  # Mark the beginning of AP mode
    while True:
        wdt.feed()
        # Timeout check for AP mode: if no configuration for 10 minutes, reboot.
        elapsed = time.time() - start_time
        if elapsed > AP_TIMEOUT:
            print("AP mode timeout reached. Rebooting device...")
            time.sleep(1)  # Short delay before rebooting
            machine.reset()
        
        try:
            s.settimeout(5.0)  # Set a timeout to prevent blocking indefinitely
            cl, addr = s.accept()
            print("Client connected from", addr)
            cl.settimeout(5.0)
            cl_file = cl.makefile('rwb', 0)
            request = b""
            # Read HTTP headers until an empty line is found
            while True:
                try:
                    line = cl_file.readline()
                except Exception as e:
                    print("Error reading from socket:", e)
                    break
                if not line or line == b'\r\n':
                    break
                request += line
            request_str = request.decode('utf-8')
            print("Request:", request_str)
            
            # Serve HTML form for GET request
            if "GET / " in request_str:
                response = """\
HTTP/1.0 200 OK

<html>
  <head>
    <title>ESP32 WiFi Config</title>
    <meta charset="utf-8">
    <style>
      body { font-family: Arial, sans-serif; margin: 20px; }
      .container { max-width: 400px; margin: auto; }
      input { margin: 5px 0; padding: 8px; width: 100%%; }
      input[type="submit"] { width: auto; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>WiFi Configuration</h1>
      <form action="/" method="post">
        <label for="ssid">SSID:</label><br>
        <input type="text" id="ssid" name="ssid" required><br>
        <label for="password">Password:</label><br>
        <input type="password" id="password" name="password" required><br>
        <input type="submit" value="Save">
      </form>
    </div>
  </body>
</html>
"""
                send_response(cl, response)
            # Handle POST request (form submission)
            elif "POST / " in request_str:
                cl_len = ure.search("Content-Length: (\d+)", request_str)
                content_length = int(cl_len.group(1)) if cl_len else 0
                body = cl_file.read(content_length).decode('utf-8')
                print("POST Body:", body)
                params = parse_post_data(body)
                ssid = params.get("ssid")
                password = params.get("password")
                if ssid and password:
                    save_wifi_config(ssid, password)
                    response = """\
HTTP/1.0 200 OK

<html>
  <head>
    <title>WiFi Config Saved</title>
    <meta charset="utf-8">
    <style>
      body { font-family: Arial, sans-serif; margin: 20px; text-align: center; }
      .message { margin-top: 50px; }
    </style>
  </head>
  <body>
    <div class="message">
      <h1>Configuration Saved!</h1>
      <p>The ESP32 will now reboot to connect to the WiFi network.</p>
    </div>
  </body>
</html>
"""
                    send_response(cl, response)
                    print("Rebooting in 3 seconds...")
                    time.sleep(3)
                    machine.reset()
                else:
                    response = """\
HTTP/1.0 400 Bad Request

<html>
  <head>
    <title>Error</title>
    <meta charset="utf-8">
    <style>
      body { font-family: Arial, sans-serif; margin: 20px; text-align: center; color: red; }
    </style>
  </head>
  <body>
    <h1>Error</h1>
    <p>Missing SSID or password.</p>
  </body>
</html>
"""
                    send_response(cl, response)
            else:
                cl.close()
        except Exception as e:
            print("Error handling client connection:", e)
            try:
                cl.close()
            except:
                pass
        wdt.feed()  # Keep feeding the watchdog periodically

def setup_wifi():
    """
    Attempts connection to stored WiFi credentials up to 3 times.
    If all attempts fail, it starts the AP mode with configuration server.
    """
    ssid, password = load_wifi_config()
    if ssid and password:
        connected = False
        for attempt in range(5):
            print("WiFi connection attempt {}/5".format(attempt + 1))
            if connect_to_wifi(ssid, password):
                connected = True
                break
            else:
                print("Attempt {} failed.".format(attempt + 1))
                time.sleep(2)
                wdt.feed()
        if not connected:
            print("Failed to connect using stored credentials. Starting AP mode.")
            start_access_point()
            start_config_server()  # Start the configuration portal
    else:
        print("No stored credentials. Starting AP mode.")
        start_access_point()
        start_config_server()  # Start the configuration portal

# Start the WiFi setup process
setup_wifi()
