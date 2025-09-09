import socket
from ctypes import *

import app

'''
A simple example for controlling a single joint move once by python

Ref: https://github.com/MrAsana/AMBER_B1_ROS2/wiki/SDK-&-API---UDP-Ethernet-Protocol--for-controlling-&-programing#2-single-joint-move-once
C++ version:  https://github.com/MrAsana/C_Plus_API/tree/master/amber_gui_4_node
     
'''
IP_ADDR = "127.0.0.1"  # ROS master's IP address


class robot_joint_position(Structure):  # ctypes struct for send
    _pack_ = 1  # Override Structure align
    _fields_ = [("cmd_no", c_uint16),  # Ref:https://docs.python.org/3/library/ctypes.html
                ("length", c_uint16),
                ("counter", c_uint32),
                ("pos0", c_float),
                ("pos1", c_float),
                ("pos2", c_float),
                ("pos3", c_float),
                ("pos4", c_float),
                ("pos5", c_float),
                ("pos6", c_float),
                ("pos7", c_float),
                ("time", c_float),
                ]


class robot_mode_data(Structure):  # ctypes struct for receive
    _pack_ = 1
    _fields_ = [("cmd_no", c_uint16),
                ("length", c_uint16),
                ("counter", c_uint32),
                ("respond", c_uint8),
                ]


async def Joint_CTRL(angle, exec_time,s):
    #s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Standard socket processes
    #try:
    #    s.bind(("0.0.0.0", 12324))
    #except OSError:
    #    Joint_CTRL(angle, exec_time)
    #    return
    deg2arc=0.017453292519943295
    payloadS = robot_joint_position(4, 44, 114514, angle[0]*deg2arc, angle[1]*deg2arc, angle[2]*deg2arc, angle[3]*deg2arc, angle[4]*deg2arc, angle[5]*deg2arc, angle[6]*deg2arc, angle[7]*deg2arc, float(exec_time))  # Fill struct for send with numbers
    # payloadS = robot_joint_position(4, 44, 114514,
    #                                 0, 0.52695047, 1.1413464, -0.12284705, -1.718193, -0.40738792, -1.64066271, 0, 5)
    #
    # payloadS = robot_joint_position(4, 44, 114514,
    #                                 -0.01571, 1.101571, 0.99565, 0.00982, 1.01121, 0.00980,  0.00162  ,0, 5)
    s.sendto(payloadS, (IP_ADDR, 26001))  # Default port is 25001
    #print("Sending: cmd_no={:d}, "
    #      "length={:d}, counter={:d},".format(payloadS.cmd_no,
    #                                          payloadS.length,
    #                                          payloadS.counter, ))
#
    #print("pos0={:f},pos1={:f},pos2={:f},"
    #      "pos3={:f},pos4={:f},"
    #      "pos5={:f},pos6={:f},"
    #      "pos7={:f},time={:f}".format(payloadS.pos0, payloadS.pos1,
    #                                   payloadS.pos2, payloadS.pos3,
    #                                   payloadS.pos4, payloadS.pos5,
    #                                   payloadS.pos6, payloadS.pos7,
    #                                   payloadS.time))
    s.settimeout(2)
    try:
        data, addr = s.recvfrom(1024)  # Need receive return
        print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)  # Convert raw data into ctypes struct to print
        print("Received: cmd_no={:d}, length={:d}, "
              "counter={:d}, respond={:d}".format(payloadR.cmd_no,
                                                  payloadR.length,
                                                  payloadR.counter,
                                                  payloadR.respond, ))
        app.param.ROS_Online_Flag = True
    except socket.timeout:
        return -1
    return
