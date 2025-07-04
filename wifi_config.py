# wifi_config.py
# pylint: disable=import-error
import time
import gc
import network
import ujson
import machine
import socket
import ure

# LED Pins
YELLOW_LED_PIN = 12
GREEN_LED_PIN = 14
yellow_led = machine.Pin(YELLOW_LED_PIN, machine.Pin.OUT)
green_led = machine.Pin(GREEN_LED_PIN, machine.Pin.OUT)
yellow_led.value(0)
green_led.value(0)

CONFIG_FILE = "wifi_config.json"

def load_wifi_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            config = ujson.load(f)
            return config.get("ssid"), config.get("password")
    except Exception as e:
        print("[WIFI] Failed to load config:", e)
        return None, None

def save_wifi_config(ssid, password):
    with open(CONFIG_FILE, "w") as f:
        ujson.dump({"ssid": ssid, "password": password}, f)
    print("[WIFI] Config saved.")

def scan_networks(wlan):
    print("[WIFI] Scanning for networks...")
    try:
        nets = wlan.scan()
        for net in nets:
            try:
                ssid = net[0].decode()
            except:
                ssid = net[0]
            print(" - Found:", ssid)
    except Exception as e:
        print("[WIFI] Scan failed:", e)

def connect_to_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    gc.collect()
    scan_networks(wlan)

    print("[WIFI] Connecting to:", ssid)
    wlan.connect(ssid, password)

    yellow_led.value(1)
    green_led.value(0)

    for i in range(60):
        if wlan.isconnected():
            print("[WIFI] Connected:", wlan.ifconfig())
            yellow_led.value(0)
            green_led.value(1)
            time.sleep(2)
            green_led.value(0)
            return True
        time.sleep(0.5)
        yellow_led.value(i % 2)

    print("[WIFI] Failed to connect.")
    yellow_led.value(0)
    green_led.value(0)
    return False

def start_access_point():
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="PasaSys Wifi Config", password="pasasys123", authmode=network.AUTH_WPA_WPA2_PSK)
    print("[WIFI] AP Mode started:", ap.ifconfig())

    yellow_led.value(1)
    green_led.value(0)
    return ap

def url_decode(s):
    res = ""
    i = 0
    while i < len(s):
        if s[i] == '+':
            res += ' '
            i += 1
        elif s[i] == '%' and i + 2 < len(s):
            try:
                res += chr(int(s[i+1:i+3], 16))
                i += 3
            except:
                res += s[i]
                i += 1
        else:
            res += s[i]
            i += 1
    return res

def parse_post_data(body):
    params = {}
    for pair in body.split("&"):
        try:
            key, val = pair.split("=")
            params[url_decode(key)] = url_decode(val)
        except:
            pass
    return params

def send_response(client, html):
    try:
        client.send(html)
    except:
        pass
    finally:
        client.close()

def start_config_server():
    """
    Starts a simple HTTP server that serves an HTML form for entering WiFi credentials.
    Reboots automatically after 5 minutes if no config is submitted.
    """
    import time
    import machine
    import socket
    import ure

    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.bind(addr)
    s.listen(1)
    print("Configuration server listening on", addr)

    # Timeout timer
    start_time = time.time()
    TIMEOUT_SECONDS = 300  # 5 minutes
    warned = False  # To only warn once at 30 seconds left

    while True:
        # ⏱ Timeout check
        elapsed = time.time() - start_time
        remaining = TIMEOUT_SECONDS - elapsed

        if remaining <= 0:
            print("[TIMEOUT] No config submitted. Rebooting...")
            yellow_led.value(0)
            machine.reset()
        elif remaining <= 30 and not warned:
            print("[WARNING] 30 seconds remaining before reboot...")
            warned = True

        # Optional: LED blink faster in last 30 seconds
        if remaining <= 30:
            yellow_led.value(1)
            time.sleep(0.2)
            yellow_led.value(0)
            time.sleep(0.2)
        else:
            yellow_led.value(1)
            time.sleep(0.5)
            yellow_led.value(0)
            time.sleep(0.5)

        try:
            cl, addr = s.accept()
            print("Client connected from", addr)
            cl.settimeout(5.0)
            cl_file = cl.makefile('rwb', 0)
            request = b""
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
