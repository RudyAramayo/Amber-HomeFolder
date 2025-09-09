import socket
from ctypes import *

import app

'''
A simple example for controlling a single joint move once by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#2-single-joint-move-once
C++ version:  https://github.com/MrAsana/C_Plus_API/tree/master/amber_gui_4_node
     
'''
IP_ADDR = "127.0.0.1"  # ROS master's IP address


class robot_joint_position(Structure):                              # ctypes struct for send
    _pack_ = 1                                                      # Override Structure align
    _fields_ = [("cmd_no", c_uint16),                               # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("mode", c_uint16),                               # Ref:https://docs.python.org/3/library/ctypes.html

                ]


class robot_mode_data(Structure):                                   # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint8),
                ]


async def initPositionMode():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)                # Standard socket processes
    s.bind(("0.0.0.0", 12330))
    payloadS = robot_joint_position(10, 9, 114514,1)               # 0 : open, 1 : close
    s.sendto(payloadS, (IP_ADDR, 26001))                                # Default port is 25001

    s.settimeout(1)
    try:
        data, addr = s.recvfrom(1024)                                       # Need receive return
        payloadR = robot_mode_data.from_buffer_copy(data)                   # Convert raw data into ctypes struct to print

    except socket.timeout:
        return -1

    payloadS = robot_joint_position(10, 9, 114514,2)               # 0 : open, 1 : close
    s.sendto(payloadS, (IP_ADDR, 26001))                                # Default port is 25001
    s.settimeout(1)
    
    #
    try:
        data, addr = s.recvfrom(1024)                                       # Need receive return

        payloadR = robot_mode_data.from_buffer_copy(data)                   # Convert raw data into ctypes struct to print

    except socket.timeout:
        return -1
    
async def deactiveMode():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)                # Standard socket processes
    s.bind(("0.0.0.0", 12330))
    payloadS = robot_joint_position(10, 9, 114514,0)               # 0 : open, 1 : close
    s.sendto(payloadS, (IP_ADDR, 26001))                                # Default port is 25001

    s.settimeout(1)
    try:
        data, addr = s.recvfrom(1024)                                       # Need receive return
        payloadR = robot_mode_data.from_buffer_copy(data)                   # Convert raw data into ctypes struct to print

    except socket.timeout:
        return -1
async def currentMode():
    #s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)                # Standard socket processes
    #s.bind(("0.0.0.0", 12330))
    #payloadS = robot_joint_position(10, 9, 114514,1)               # 0 : open, 1 : close
    #s.sendto(payloadS, (IP_ADDR, 26001))                                # Default port is 25001

    #s.settimeout(1)
    #try:
    #    data, addr = s.recvfrom(1024)                                       # Need receive return
    #    payloadR = robot_mode_data.from_buffer_copy(data)                   # Convert raw data into ctypes struct to print

    #except socket.timeout:
    #    return -1

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)                # Standard socket processes
    s.bind(("0.0.0.0", 12330))
    payloadS = robot_joint_position(10, 9, 114514,4)               # 0 : open, 1 : close
    s.sendto(payloadS, (IP_ADDR, 26001))                                # Default port is 25001

    s.settimeout(1)
    try:
        data, addr = s.recvfrom(1024)                                       # Need receive return
        payloadR = robot_mode_data.from_buffer_copy(data)                   # Convert raw data into ctypes struct to print

    except socket.timeout:
        return -1