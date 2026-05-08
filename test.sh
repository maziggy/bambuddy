docker exec bambuddy python3 -c '
import socket
host = "192.168.255.133"
ports = [80, 322, 990, 2024, 2025, 2026, 3000, 3002, 6000, 8883]
for p in ports:
	s = socket.socket()
	s.settimeout(3)
	try:
		s.connect((host, p))
		print(f"{p} open")
	except OSError as e:
		print(f"{p} closed ({e.__class__.__name__}: {e})")
	finally:
		s.close()
'
