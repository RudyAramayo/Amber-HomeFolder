# Readme

In order to improve the response frequency to user operations, we switch high-frequency control to LCM communication. This project provides a short DEMO of LCM communication.

LCM communication supports many languages, but this project is written in python.

see https://lcm-proj.github.io/lcm/content/tutorial-python.html

## What channel should I choose?

#### For newer versions

In order to distinguish multiple robot arms in the same LAN, the channel name is allowed to be changed. The channel name consists of a prefix and a suffix. Generally, only the prefix can be changed.

As described in `launch.json`

```json
  "LCM_Interface": {
    "Prefix": "Amber_",
    "PositionCommand": "PosCmd",
    "ArmStatus": "ArmStatus",
    "PositionPlan": "PosPlan",
    "PositionTask": "PosTask",
    "InverseKinematicsTask": "IKTask",
    "IK_SolverRespond": "SolverRespond",
    "ForwardKinematicsTask": "FKTask",
    "SetArmMode": "SetMode",
    "GripperControl": "GripperCtrl"
  },
```

We send the `posCmd_t` message to the `Amber_PosCmd` channel to control the position of the robotic arm.

We receive the `armStatus_t` message to the `Amber_ArmStatus` channel.

#### For older version

Older versions cannot change the channel name

We send the `rosCommand_t` message to the `RosCommand` channel to control the position of the robotic arm.

We receive the `rosData_t` message to the `RosData` channel.

## How to enable multicast on your network card

1. Check network interface name

   ```bash
   ifconfig
   ```

2. Run the following two commands to explicitly enable UDP multicast and add routing tables

   ```bash
   sudo ifconfig eth0 multicast
   sudo route add -net 239.255.76.67 netmask 255.255.255.255 dev eth0
   # Check whether the addition is successful
   route -n
   ```

   **The name of "eth0" is determined by the name of the network card on your computer (or industrial computer) and needs to be changed after querying, usually it may be a name like "enpXs0"**

3. Set the TTL value, which is related to the number of routers that the broadcast command can pass through

   ```bash
   export LCM_DEFAULT_URL=udpm://239.255.76.67:7667?ttl=10
   ```

4. Use ssh to log in to the control box and repeat the above operations

   Note that you may need to restart the control program after configuring the parameters.

   ```bash
   sudo killall amber_core
   cd amber_core_7
   ./amber_core
   ```

5. If you want to persist this configuration, add step 2 and 3  to the second line of the file /etc/rc.local

## What information do they convey?

For position control we use posCmd_t (formerly rosCommand_t)

```c
package lcmTypes;

struct posCmd_t
{
    double jointTarget [7];
}
```

It just sends a set of positions and all the joints on the arm will immediately try to rotate to that position

For getting status we use armStatus_t (formerly rosData_t)

```c
package lcmTypes;

struct armStatus_t
{
    double jointPosition [7];
    double jointVelocity [7];
    double jointCurrent  [7];
    double jointStatus   [7];
}
```

All lcm structure prototypes can be found in .\rawLcm\ so you can adapt to any lcm communication supported language

## Supported platforms / languages

https://lcm-proj.github.io/lcm/index.html

- Platforms:
  - GNU/Linux
  - OS X
  - Windows
  - Any POSIX-1.2001 system (e.g., Cygwin, Solaris, BSD, etc.)
- Languages
  - C
  - C++
  - C#
  - Java
  - Lua
  - MATLAB
  - Python (3.6 and later)