import csv
import time
from ctypes import *
import socket

RUNTIME_T = 3
index = 0

p0 = []
p1 = []

with open('JointKFOut.0.csv', 'rt') as f:
    cr = csv.reader(f)
    for row in cr:
        print(row)
        p0.append(row)  # 将test.csv内容读入列表l，每行为其一个元素，元素也为list
        index += 1
with open('JointKFOut.1.csv', 'rt') as f:
    cr = csv.reader(f)
    for row in cr:
        print(row)
        p1.append(row)  # 将test.csv内容读入列表l，每行为其一个元素，元素也为list



def waitFunc():
    # input()
    time.sleep(4)


IP_ADDR1 = "127.0.0.1"  # ROS master's IP address


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


s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # Standard socket processes
s.bind(("0.0.0.0", 12321))


def UDP_Send_0(payload, IP_ADDR):
    s.sendto(payload, (IP_ADDR, 26001))  # Default port is 25001

    print("Sending: cmd_no={:d}, "
          "length={:d}, counter={:d},".format(payload.cmd_no,
                                              payload.length,
                                              payload.counter, ))

    print("pos0={:f},pos1={:f},pos2={:f},"
          "pos3={:f},pos4={:f},"
          "pos5={:f},pos6={:f},"
          "pos7={:f},time={:f}".format(payload.pos0, payload.pos1,
                                       payload.pos2, payload.pos3,
                                       payload.pos4, payload.pos5,
                                       payload.pos6, payload.pos7,
                                       payload.time))

    s.settimeout(3)
    try:

        data, addr = s.recvfrom(1024)  # Need receive return
        print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)  # Convert raw data into ctypes struct to print
        print("Received: cmd_no={:d}, length={:d}, "
              "counter={:d}, respond={:d}".format(payloadR.cmd_no,
                                                  payloadR.length,
                                                  payloadR.counter,
                                                  payloadR.respond, ))
    except socket.timeout:
        print("timeout0!")

def UDP_Send_1(payload, IP_ADDR):
    s.sendto(payload, (IP_ADDR, 26002))  # Default port is 25001

    print("Sending: cmd_no={:d}, "
          "length={:d}, counter={:d},".format(payload.cmd_no,
                                              payload.length,
                                              payload.counter, ))

    print("pos0={:f},pos1={:f},pos2={:f},"
          "pos3={:f},pos4={:f},"
          "pos5={:f},pos6={:f},"
          "pos7={:f},time={:f}".format(payload.pos0, payload.pos1,
                                       payload.pos2, payload.pos3,
                                       payload.pos4, payload.pos5,
                                       payload.pos6, payload.pos7,
                                       payload.time))

    s.settimeout(3)
    try:

        data, addr = s.recvfrom(1024)  # Need receive return
        print("Receiving: ", data.hex())
        payloadR = robot_mode_data.from_buffer_copy(data)  # Convert raw data into ctypes struct to print
        print("Received: cmd_no={:d}, length={:d}, "
              "counter={:d}, respond={:d}".format(payloadR.cmd_no,
                                                  payloadR.length,
                                                  payloadR.counter,
                                                  payloadR.respond, ))
    except socket.timeout:
        print("timeout1!")


UDP_Send_0(robot_joint_position(4, 44, 1, 0, 0, 0, 0, 0, 0, 0, 0, RUNTIME_T), IP_ADDR1)
UDP_Send_1(robot_joint_position(4, 44, 2, 0, 0, 0, 0, 0, 0, 0, 0, RUNTIME_T), IP_ADDR1)

waitFunc()
while True:
    for i in range (index):
        UDP_Send_1(robot_joint_position(int(p1[i][0]),int(p1[i][1]),int(p1[i][2]),float(p1[i][3]),float(p1[i][4]),float(p1[i][5]),float(p1[i][6]),float(p1[i][7]),float(p1[i][8]),float(p1[i][9]),float(p1[i][10]), RUNTIME_T), IP_ADDR1)
        UDP_Send_0(robot_joint_position(int(p0[i][0]),int(p0[i][1]),int(p0[i][2]),float(p0[i][3]),float(p0[i][4]),float(p0[i][5]),float(p0[i][6]),float(p0[i][7]),float(p0[i][8]),float(p0[i][9]),float(p0[i][10]), RUNTIME_T), IP_ADDR1)
        waitFunc()

