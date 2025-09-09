import socket
from ctypes import *


#
# Created by TATQwQ on 23-9-12.
# Amber Robotics Tuning Tool
#


class Udp2canFloat(Structure):  # ctypes struct for receive
    _pack_ = 1  # Override Structure align
    _fields_ = [
        ("actuator_ID", c_uint8),
        ("verb", c_uint8),
        ("cmd", c_uint8),
        ("data", c_float),  # ctypes array
    ]


def float_data_through_udp(ip_addr, actuator_id, verb, cmd, data=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 12321))
    fcandata = Udp2canFloat()
    fcandata.verb = verb
    fcandata.cmd = cmd
    fcandata.actuator_ID = actuator_id
    fcandata.data = data
    s.settimeout(1)

    s.sendto(fcandata, (ip_addr, 24001))
    try:
        data, addr = s.recvfrom(1024)
        # print("Receiving: ", data.hex())
        payload_r = Udp2canFloat.from_buffer_copy(data)
        return [payload_r.actuator_ID, payload_r.data]
    except socket.timeout:
        s.close()
        return [-1, -1]


class Udp2canInt(Structure):  # ctypes struct for receive
    _pack_ = 1  # Override Structure align
    _fields_ = [
        ("actuator_ID", c_uint8),
        ("verb", c_uint8),
        ("cmd", c_uint8),
        ("data", c_uint32),  # ctypes array
    ]


def int_data_through_udp(ip_addr, actuator_id, verb, cmd, data=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 12321))
    fcandata = Udp2canInt()
    fcandata.verb = verb
    fcandata.cmd = cmd
    fcandata.actuator_ID = actuator_id
    fcandata.data = data
    s.settimeout(1)
    s.sendto(fcandata, (ip_addr, 24001))
    try:
        data, addr = s.recvfrom(1024)
        # print("Receiving: ", data.hex())
        payload_r = Udp2canInt.from_buffer_copy(data)
        return [payload_r.actuator_ID, payload_r.data]
    except socket.timeout:
        s.close()
        return [-1, False]


class Attribute:
    def __init__(self, ip_addr="127.0.0.1", actuator_id=1):
        # self._position = None
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id

    @property
    def version(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 1)
        return hex(result[1])

    @property
    def mode(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 2)
        return result[1]

    @mode.setter
    def mode(self, data):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, data)

    @property
    def pole_number(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 3)
        return result[1]

    @pole_number.setter
    def pole_number(self, data):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 3, data)

    @property
    def gear(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 6)
        return result[1]

    @gear.setter
    def gear(self, data):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 6, data)

    @property
    def zero_position(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 5)
        return result[1]

    @zero_position.setter
    def zero_position(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 5, data)

    @property
    def rotation_direction(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 4)
        return result[1]

    @rotation_direction.setter
    def rotation_direction(self, data):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 4, data)


class Position:
    def __init__(self, ip_addr="127.0.0.1", actuator_id=1):
        # self._position = None
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id

    def rotate_to(self, degree):
        return float_data_through_udp(self.ip_addr, self.actuator_id, 1, 11, degree)

    def set_position(self, degree):
        return float_data_through_udp(self.ip_addr, self.actuator_id, 1, 11, degree)

    def rotate(self, delta):
        now = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 10)
        return float_data_through_udp(
            self.ip_addr, self.actuator_id, 1, 11, delta + now[1]
        )

    @property
    def now(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 10)
        return result[1]

    @property
    def kp(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 12)
        return result[1]

    @kp.setter
    def kp(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 12, data)

    @property
    def ki(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 13)
        return result[1]

    @ki.setter
    def ki(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 13, data)

    @property
    def target(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 11)
        return result[1]

    @target.setter
    def target(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 11, data)

    @property
    def up_limit(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 14)
        return result[1]

    @up_limit.setter
    def up_limit(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 14, data)

    @property
    def down_limit(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 15)
        return result[1]

    @down_limit.setter
    def down_limit(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 15, data)

    @property
    def limit_switch(self):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 0, 16)
        return result[1]

    @limit_switch.setter
    def limit_switch(self, data):
        result = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 16, data)


class Speed:
    def __init__(self, ip_addr="127.0.0.1", actuator_id=1):
        # self._position = None
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id

    @property
    def now(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 20)
        return result[1]

    @property
    def kp(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 22)
        return result[1]

    @kp.setter
    def kp(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 22, data)

    @property
    def ki(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 23)
        return result[1]

    @ki.setter
    def ki(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 23, data)

    @property
    def target(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 21)
        return result[1]

    @target.setter
    def target(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 21, data)

    @property
    def acceleration(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 24)
        return result[1]

    @acceleration.setter
    def acceleration(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 24, data)

    @property
    def deceleration(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 25)
        return result[1]

    @deceleration.setter
    def deceleration(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 25, data)

    @property
    def speed_limit(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 26)
        return result[1]

    @speed_limit.setter
    def speed_limit(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 26, data)


class Current:
    def __init__(self, ip_addr="127.0.0.1", actuator_id=1):
        # self._position = None
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id

    @property
    def now(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 30)
        return result

    @property
    def kp(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 32)
        return result[1]

    @kp.setter
    def kp(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 32, data)

    @property
    def ki(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 33)
        return result[1]

    @ki.setter
    def ki(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 33, data)

    @property
    def target(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 31)
        return result[1]

    @target.setter
    def target(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 31, data)

    @property
    def current_limit(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 34)
        return result[1]

    @current_limit.setter
    def current_limit(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 34, data)


class Hybrid:
    def __init__(self, ip_addr="127.0.0.1", actuator_id=1):
        # self._position = None
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id

    @property
    def kt(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 50)
        return result

    @kt.setter
    def kt(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 50, data)

    @property
    def force(self):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 51)
        return result

    @force.setter
    def force(self, data):
        result = float_data_through_udp(self.ip_addr, self.actuator_id, 1, 51, data)


class AmberActuator:
    def __init__(self, actuator_id=1, ip_addr="127.0.0.1"):
        self.version = 0.1
        self.ip_addr = ip_addr
        self.actuator_id = actuator_id
        self.position = Position(ip_addr, actuator_id)
        self.speed = Speed(ip_addr, actuator_id)
        self.current = Current(ip_addr, actuator_id)
        self.attribute = Attribute(ip_addr, actuator_id)
        self.hybrid = Hybrid(ip_addr, actuator_id)

    def rotate_to(self, degree):
        return float_data_through_udp(self.ip_addr, self.actuator_id, 1, 11, degree)

    def set_position(self, degree):
        return float_data_through_udp(self.ip_addr, self.actuator_id, 1, 11, degree)

    def rotate(self, delta):
        now = float_data_through_udp(self.ip_addr, self.actuator_id, 0, 10)
        return float_data_through_udp(
            self.ip_addr, self.actuator_id, 1, 11, delta + now[1]
        )
    def calibrate(self):
        var0 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 0)
        if var0[0] == self.actuator_id:
            var1 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 1)
            if var1[0] == self.actuator_id:
                int_data_through_udp(self.ip_addr, self.actuator_id, 1, 41, 0)

                return True
        return False
    def save(self):
        return float_data_through_udp(self.ip_addr, self.actuator_id, 1, 42)

    def init_zero_position(self):
        return int_data_through_udp(self.ip_addr, self.actuator_id, 1, 40, 0)

    def position_mode(self):
        var0 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 0)
        if var0[0] == self.actuator_id:
            var1 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 1)
            if var1[0] == self.actuator_id:
                var2 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 2)
                if var2[0] == self.actuator_id:
                    return True
        return False

    def speed_mode(self):
        var0 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 0)
        if var0[0] == self.actuator_id:
            var1 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 1)
            if var1[0] == self.actuator_id:
                var2 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 3)
                if var2[0] == self.actuator_id:
                    return True
        return False

    def current_mode(self):
        var0 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 0)
        if var0[0] == self.actuator_id:
            var1 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 1)
            if var1[0] == self.actuator_id:
                var2 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 4)
                if var2[0] == self.actuator_id:
                    return True
        return False

    def hybrid_mode(self):
        var0 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 0)
        if var0[0] == self.actuator_id:
            var1 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 1)
            if var1[0] == self.actuator_id:
                var2 = int_data_through_udp(self.ip_addr, self.actuator_id, 1, 2, 6)
                if var2[0] == self.actuator_id:
                    return True
        return False


if __name__ == "__main__":
    amber_robot = AmberActuator()
