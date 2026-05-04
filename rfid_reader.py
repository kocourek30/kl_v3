import serial
import requests

def read_rfid_code():
    try:
        ser = serial.Serial('COM3', 9600, timeout=3)
        code = ser.readline().decode('utf-8').strip()
        ser.close()
        return code
    except Exception as e:
        print(f"Chyba při čtení RFID: {e}")
        return None

if __name__ == "__main__":
    while True:
        code = read_rfid_code()
        if code:
            print(f"Přečtený RFID kód: {code}")
            # Poslat na API server.
            requests.post("http://localhost:8000/api/rfid_login/", data={"rfid": code})
