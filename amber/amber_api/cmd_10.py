import socket
import time
from ctypes import *
import argparse

'''
A simple example for controlling a single joint move once by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#2-single-joint-move-once
C++ version:  https://github.com/MrAsana/C_Plus_API/tree/master/amber_gui_4_node
     
'''
parser = argparse.ArgumentParser(description="Example of argparse with default values.")
parser.add_argument("--ip", type=str, default="10.0.0.5", help="IP (default: %(default)s)")
parser.add_argument("--port", type=int, default=26002, help="Port (default: %(default)s)")

args = parser.parse_args()

#print(f"ip {args.ip}")
#print(f"port {args.port}")


class robot_joint_position(Structure):  # ctypes struct for send
    _pack_ = 1  # Override Structure align
    _fields_ = [("cmd_no", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("mode", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html

                ]

class robot_joint_position_getMode(Structure):                              # ctypes struct for send
    _pack_ = 1                                                      # Override Structure align
    _fields_ = [("cmd_no", c_uint16),                               # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("joint_id", c_uint32),
                ]
                
class robot_mode_data(Structure):
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint8),
                ]

#this funtion has a different robot_joint_position... does that matter?!?!
def get_work_mode(IP_ADDR=args.ip, port=args.port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    #s.bind(("0.0.0.0", 12430))
    payloadS = robot_joint_position_getMode(110, 12, 0, 8)
    s.sendto(payloadS, (IP_ADDR, port))
    s.settimeout(1)

    try:
        data, addr = s.recvfrom(1024)                                       # Need receive return
        #print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)
        return payloadR.respond
    except socket.timeout:
        return -1

def check_activated(IP_ADDR=args.ip, port=args.port, timeout=2):
    time_passed = 0
    while time_passed < timeout:
        activated = 0
        mode_now = get_work_mode(IP_ADDR=IP_ADDR, port=port)
        for i in range(7):
            if (mode_now[i] == 1):
                activated += mode_now[i]
            if activated > 6:
                return True
        time.sleep(0.1)
        time_passed += 0.1
    print("\033[31m    Mode switching failed    \033[0m")
    return False


def on_active_mode(IP_ADDR=args.ip, port=args.port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    #s.bind(("0.0.0.0", 12430))
    payloadS = robot_joint_position(10, 9, 114514, 1)
    s.sendto(payloadS, (IP_ADDR, port))
    try:
        data, addr = s.recvfrom(1024)
        payloadR = robot_mode_data.from_buffer_copy(data)
        s.close()
        return payloadR
    except socket.timeout:
        return -1


def on_position_mode(IP_ADDR=args.ip, port=args.port):
    on_active_mode(IP_ADDR=IP_ADDR, port=port)
    if check_activated(IP_ADDR=IP_ADDR, port=port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        #s.bind(("0.0.0.0", 12430))
        payloadS = robot_joint_position(10, 9, 114514, 1)

        payloadS = robot_joint_position(10, 10, 114514, 2)
        s.sendto(payloadS, (IP_ADDR, port))
        try:
            data, addr = s.recvfrom(1024)
            payloadR = robot_mode_data.from_buffer_copy(data)
            s.close()
            return payloadR
        except socket.timeout:
            return -1
    else:
        return -1


def on_current_mode(IP_ADDR=args.ip, port=args.port):
    on_active_mode(IP_ADDR=IP_ADDR, port=port)
    if check_activated(IP_ADDR=IP_ADDR, port=port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        #s.bind(("0.0.0.0", 12430))
        payloadS = robot_joint_position(10, 10, 114514, 4)
        s.sendto(payloadS, (IP_ADDR, port))
        try:
            data, addr = s.recvfrom(1024)
            payloadR = robot_mode_data.from_buffer_copy(data)
            s.close()
            return payloadR
        except socket.timeout:
            return -1
    else:
        return -1


def on_deactivate_mode(IP_ADDR=args.ip, port=args.port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    #s.bind(("0.0.0.0", 12330))
    payloadS = robot_joint_position(10, 9, 114514, 0)
    s.sendto(payloadS, (IP_ADDR, port))
    s.settimeout(1)
    try:
        data, addr = s.recvfrom(1024)
        payloadR = robot_mode_data.from_buffer_copy(data)
        s.close()
        return payloadR
    except socket.timeout:
        return -1
