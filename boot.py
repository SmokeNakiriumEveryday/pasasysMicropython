# boot.py
#pylint: disable=import-error,unused-import
import gc
import network
import time
import ujson
import machine  # Required for relay

# ✅ IMMEDIATE safety: turn heating rod OFF
relay = machine.Pin(15, machine.Pin.OUT, value=0)
relay.value(0)

gc.collect()
print("[BOOT] Memory before Wi-Fi:", gc.mem_free()) #pylint: disable=no-member

# Load credentials
try:
    with open("wifi_config.json", "r") as f:
        config = ujson.load(f)
        ssid = config.get("ssid")
        password = config.get("password")
except Exception as e:
    print("[BOOT] Failed to load Wi-Fi config:", e)
    ssid = None
    password = None

wifi_ok = False

# Attempt connection
if ssid and password:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    print("[BOOT] Connecting to:", ssid)

    for i in range(20):
        if wlan.isconnected():
            print("[BOOT] Wi-Fi connected:", wlan.ifconfig())
            wifi_ok = True
            break
        time.sleep(0.5)

# Write Wi-Fi status flag
with open("wifi_status.flag", "w") as f:
    f.write("ok" if wifi_ok else "fail")

gc.collect()
print("[BOOT] Memory after Wi-Fi:", gc.mem_free()) #pylint: disable=no-member
time.sleep(1)
