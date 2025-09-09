lcm_recv_channel = "Amber_ArmStatus"
lcm_send_channel = "Amber_PosCmd"
import time
import socket

from h2o_wave import main, app, Q, ui, on, handle_on, data, AsyncSite, graphics as g

from typing import Optional, List
import UDP_CMD.cmd_4
import UDP_CMD.cmd_1
import UDP_CMD.cmd_2
import UDP_CMD.cmd_3
import UDP_CMD.cmd_6
import UDP_CMD.cmd_7
import UDP_CMD.cmd_9
import UDP_CMD.cmd_10
import UDP_CMD.cmd_110
import UDP_CMD.get_ip
import UDP_CMD.meshcat_bridge
import asyncio
import concurrent.futures
from h2o_wave import main, app, Q, ui
import lcm
from datetime import datetime

# Use for page cards that should be removed when navigating away.
# For pages that should be always present on screen use q.page[key] = ...
import img



class Record:
    def __init__(self, j1, j2, j3, j4, j5):
        self.j1 = j1
        self.j2 = j2
        self.j3 = j3
        self.j4 = j4
        self.j5 = j5


class param:
    record_list = []
    s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s6 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s110 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ROS_Online_Flag = False
    ROS_Power_button = True
    jointMode = [10,10,10,10,10,10,10]
    jointModeNames = ['Deactivated','Activated','Position Mode','Velocity','Current','reserve','reserve','reserve','reserve','reserve','OFFLine']
    jointNow = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    jointTarget = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    cartesianNow = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    cartesianTarget = [0.0, 0.0, 6810, 0.0, 0.0, 0.0, 0.0]
    cartesianNames = ['X Axis (mm)', 'Y Axis (mm)', 'Z Axis (mm)',
                      'Roll', 'Pitch', 'Yaw',
                      'Arm Angle']
    controlModeNames = ['Cartesian Control', 'Joint Control    ']
    exec_time = 0.5
    PageFlag = 0
    ROS_status_reporter_ON_Flag = False
    Reporter_ON_Flag = False
    Monitor_Reporter_ON_Flag = False
    Meshcat_ON_Flag = False
    delta_degree = 0
    step = 0
    Real_Time_Toggle = True
    Real_Time_Lable = ['Realtime Control :  🔴 OFF', 'Realtime Control : 🟢 ON ']
    HostIP = ''
    theme = 'amber-dark-mode'  # 'default'
    RecorderON = False
    isReplaying = False
    isRecording = False
    isDragMode = False
    readyFilename = "drag-"+datetime.now().strftime('%y-%m-%d-%H-%M-%S')
    gripperForce = 5


def add_card(q, name, card):
    q.client.cards.add(name)
    q.page[name] = card
    return card


# Remove all the cards related to navigation.
def clear_cards(q, ignore: Optional[List[str]] = []) -> None:
    if not q.client.cards:
        return

    for name in q.client.cards.copy():
        if name not in ignore:
            del q.page[name]
            q.client.cards.remove(name)


@on('#page1')
async def page1(q: Q):
    # FOR MODE CONTROL
    tableRow = []
    mylist = [f for f in glob.glob("./RecordedFiles/*.csv")]
    for i in range (len(mylist)):
        tableRow.append(ui.table_row(name=f'{i}',  cells=[mylist[i]]))

    if q.args['Deactivate']:
        await UDP_CMD.cmd_10.deactiveMode()
        await UDP_CMD.cmd_110.get_work_mode()
        await asyncio.sleep(0.2)
        #await q.page['jointMode0'].save()
    if q.args['PositionMode']:
        await UDP_CMD.cmd_10.initPositionMode()
        await UDP_CMD.cmd_110.get_work_mode()
        await asyncio.sleep(0.2)
        


    await UDP_CMD.cmd_110.get_work_mode()
    # FOR MODE CONTROL


    if param.PageFlag != 1:
        param.PageFlag = 1
        await sync_helper(q)
    if q.args['TimeSlider']:
        param.exec_time = q.app['exec_time'] = q.args['TimeSlider']
    if q.args['realtime_toggle']:
        param.Real_Time_Toggle = True
    if q.args['realtime_toggle' ] is not True:
        param.Real_Time_Toggle = False

    for i in range(7):
        if q.args[f'jointSlider{i}'] is not None:
            param.jointTarget[i] = q.app[f'joint{i}'] = q.args[f'jointSlider{i}']
        if q.args[f'go_zero_button{i}']:
            param.jointTarget[i] = q.app[f'joint{i}'] = 0
            if q.args['realtime_toggle'] is True or q.args['play_button']:
                await q.run(UDP_CMD.cmd_4.Joint_CTRL, param.jointTarget, param.exec_time, param.s4)
        if q.args[f'set_joint_button{i}']:
            param.jointTarget[i] = q.app[f'joint{i}'] = float(q.args[f'jointText{i}'])
    if q.args['realtime_toggle'] is True or q.args['play_button']:
        await q.run(UDP_CMD.cmd_4.Joint_CTRL, param.jointTarget, param.exec_time, param.s4)
    if q.args[f'go_home_pos']:
        for i in range(7):
            param.jointTarget[i] = q.app[f'joint{i}'] = 0
        await q.run(UDP_CMD.cmd_4.Joint_CTRL, param.jointTarget, param.exec_time, param.s4)

    if q.args['set_time_button']:
        param.exec_time = float(q.args['time_textbox'])
    jointPage2sync = [q.page['jointGauge0'], q.page['jointGauge1'], q.page['jointGauge2'],
                      q.page['jointGauge3'], q.page['jointGauge4'], q.page['jointGauge5'],
                      q.page['jointGauge6']
                      ]
    cartesianPage2sync = [q.page['cartesianGauge0'], q.page['cartesianGauge1'],
                          q.page['cartesianGauge2'], q.page['cartesianGauge3'], q.page['cartesianGauge4'],
                          q.page['cartesianGauge5'], q.page['cartesianGauge6'], q.page['cartesianGauge7']
                          ]
    
    future = asyncio.ensure_future(reporter(q, jointPage2sync, cartesianPage2sync))
    # asyncio.ensure_future(sync_helper(q))

    q.page['sidebar'].value = '#page1'
    clear_cards(q)  # When routing, drop all the cards except of the main ones (header, sidebar, meta).
    # curves = 'linear smooth step step-after step-before'.split()
    add_card(q, 'monitor', ui.tall_series_stat_card(
        box=ui.box('sidebar_r', height='100px', width='350px', size=90),
        title='Delta Degree',
        value=str('%.4f' % param.delta_degree + '°'),
        aux_value='',
        data=dict(qux=param.delta_degree, quux=param.step),
        # data=dict(qux=val, quux=pc / 100),
        plot_type='area',
        plot_category='quux',
        plot_value='qux',
        plot_color='green',
        plot_data=data('quux qux', -100),
        plot_zero_value=-145,
        # plot_curve='step-after'
    ))

    if param.Monitor_Reporter_ON_Flag is False:
        asyncio.ensure_future(monitorReporter(q, q.page['monitor']))

    #add_card(q, 'fileTable',ui.form_card
    #    (box=ui.box('sidebar_r', height='280px', width='350px', size=100), 
    #    items=[ui.table(
    #    name='file_table',height='280px',
    #    columns=[ui.table_column(name='name', label='Name',cell_overflow='wrap')],
    #    rows=tableRow,
    #)
    #   ]))
    if q.args['gripperSlider']:
        param.gripperForce  = q.args['gripperSlider']
    if q.args['gripper_calibrate']:
        await UDP_CMD.cmd_7.gripperCalibrate()
    if q.args['gripper_open']:
        await UDP_CMD.cmd_9.gripperCtrl(0,int(param.gripperForce))
    if q.args['gripper_close']:
        await UDP_CMD.cmd_9.gripperCtrl(1,int(param.gripperForce))


    add_card(q, 'joint_ctrl', ui.form_card(box=ui.box('sidebar_r', height='280px', width='350px', size=100), items=[
        ui.text_l(param.Real_Time_Lable[param.Real_Time_Toggle]),
        ui.toggle(name='realtime_toggle', label='Realtime Control', value=param.Real_Time_Toggle, trigger=True),
        ui.separator(label=''),
        ui.text_xl('Control Settings'),
        # ui.text_l('Duration Time (s)' + str(exec_time)),
        ui.slider(name='TimeSlider', label='Duration Time', value=param.exec_time, min=0.5, max=10, step=0.1,
                  trigger=True),
        ui.textbox(name='time_textbox', label='', value='%.1f' % param.exec_time),
        ui.buttons(items=[
            ui.button(name='play_button', label='Play', icon='Next'),
            ui.button(name='set_time_button', label='Set Time', icon='Timer'),

        ]),
        ui.button(name=f'go_home_pos', label='Go Home Position', icon='Home'),
        #ui.button(name=f'add_key_point', label='Add Key Point', icon='EditNote'),

        ui.separator(label=''),
        ui.text_l("Gripper Control"),
        ui.buttons(items=[
            
            ui.button(name='gripper_open', label='Open'),
            ui.button(name='gripper_close', label='Close'),
            ui.button(name='gripper_calibrate', label='Calibrate'),
        ]),
        ui.slider(name='gripperSlider', label='Force', value=param.gripperForce, min=2, max=20, step=1,
          trigger=True),

    ]))

    for i in range(7):
        add_card(q, f'jointGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL{i + 1}', height='150px', width='150px', size='0'),
            title=f'Joint {i + 1}',
            value=str('%.2f' % (param.jointNow[i] * 57.29)) + '°',
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',

            plot_color='#2196f3',
            progress=1 - ((param.jointNow[i] * 57.29 + 145) / 290)
        ))

        add_card(q, f'jointForm{i}',
                 ui.form_card(box=ui.box(f'horizontalL{i + 1}', height='150px', width='350px', size=1), items=[
                     ui.slider(name=f'jointSlider{i}', label=f'Joint {i + 1}', value=round(param.jointTarget[i], 2),
                               min=-120,
                               max=120,
                               step=0.1, trigger=True),
                     ui.inline(items=[ui.textbox(name=f'jointText{i}', label='', value='%.2f' % param.jointTarget[i]),
                                      ui.buttons(justify='end', items=[
                                          ui.button(name=f'go_zero_button{i}', label='Go Zero', icon='Reset'),
                                          ui.button(name=f'set_joint_button{i}', label='Update', icon='ChevronUpMed')])

                                      ])

                 ]))

    for i in range(2):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value='%.2f' % (param.cartesianNow[i] * 1000),
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(625 + float('%.2f' % (param.cartesianNow[i] * 1000))) / 931.2)
                 )

    for i in range(2, 3):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value=str('%.2f' % (param.cartesianNow[i] * 1000)),
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(300 + float('%.2f' % (param.cartesianNow[2] * 1000))) / 806.1)
                 )

    for i in range(3, 6):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value='%.2f' % (param.cartesianNow[i] * 57.29) + "°",
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(180 + float('%.2f' % (param.cartesianNow[i] * 57.29))) / 360
        ))
    multiline_content = str(
        f"4,44,114514,{round(param.jointTarget[0] / 57.29, 2)},{round(param.jointTarget[1] / 57.29, 2)},{round(param.jointTarget[2] / 57.29, 2)},{round(param.jointTarget[3] / 57.29, 2)},{round(param.jointTarget[4] / 57.29, 2)},{round(param.jointTarget[5] / 57.29, 2)},{round(param.jointTarget[6] / 57.29, 2)},{round(param.jointTarget[7] / 57.29, 2)},{param.exec_time}")
    #add_card(q, f'copy', ui.form_card(
    #    box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
    #    items=[ui.copyable_text(label='Copyable UDP Protocol Payload', value=multiline_content,
    #                            multiline=True, height='100px')]
    #))
    add_card(q, f'jointMode0',ui.form_card(box=ui.box(f'horizontalL0', height='150px', width='280px', size=1), items=[
        ui.text_xl('Actuator Mode'),
        ui.facepile(max=9,items=[ 
        ui.persona(title=param.jointModeNames[param.jointMode[0]]),
        ui.persona(title=param.jointModeNames[param.jointMode[1]]),
        ui.persona(title=param.jointModeNames[param.jointMode[2]]),
        ui.persona(title=param.jointModeNames[param.jointMode[3]]),
        ui.persona(title=param.jointModeNames[param.jointMode[4]]),
        ui.persona(title=param.jointModeNames[param.jointMode[5]]),
        ui.persona(title=param.jointModeNames[param.jointMode[6]]),
    ]),ui.buttons( items=[ui.button(name='PositionMode', label='PositionMode', primary=True,icon = 'Warning'),
    ui.button(name='Deactivate', label='Deactivate',icon = 'Warning')])

    ]))
    #add_card(q, f'Placeholder', ui.large_stat_card(
    #    box=ui.box(f'horizontalL0', height='150px', width='150px', size=1),
    #    title='Model',
    #    value='Amber Lucid ONE',
    #    aux_value='',
    #    data=dict(qux=845, quux=0.8),
    #    caption='',
    #))


async def sync_helper(q: Q):
    for i in range(7):
        param.jointTarget[i] = param.jointNow[i] * 57.29
    else:
        for i in range(3):
            param.cartesianTarget[i] = round(param.cartesianNow[i] * 1000, 2)
        for i in range(3, 7):
            param.cartesianTarget[i] = round(param.cartesianNow[i] * 57.29, 2)


async def reporter(q: Q, jointPage2sync, cartesianPage2sync):
    if (param.Reporter_ON_Flag == False):
        param.Reporter_ON_Flag = True
        while True:
            for i in range(7):
                jointPage2sync[i].value = str('%.2f' % (param.jointNow[i] * 57.29)) + "°"
                jointPage2sync[i].progress = 1 - ((param.jointNow[i] * 57.29 + 145) / 290)

            for i in range(3):
                cartesianPage2sync[i].value = '%.2f' % (param.cartesianNow[i] * 1000)
                cartesianPage2sync[i].progress = (625 + float('%.2f' % (param.cartesianNow[i] * 1000))) / 931.2
            cartesianPage2sync[2].progress = (300 + float('%.2f' % (param.cartesianNow[2] * 1000))) / 806.1
            for i in range(3, 7):
                cartesianPage2sync[i].value = '%.2f' % (param.cartesianNow[i] * 57.29) + "°"
                cartesianPage2sync[i].progress = (180 + float('%.2f' % (param.cartesianNow[i] * 57.29))) / 360

            await q.page.save()
            await q.sleep(0.5)


async def monitorReporter(q: Q, monitor):
    if (param.Monitor_Reporter_ON_Flag == False):
        param.Monitor_Reporter_ON_Flag = True
        while True:
            param.step = param.step + 1
            delta_degree = 0
            for i in range(7):
                delta_degree = abs(param.jointTarget[i] - (param.jointNow[i] * 57.29)) + delta_degree

            # for i in range (9):
            #    param.delta_degree[i+1] = param.delta_degree[i]
            monitor.value = str('%.4f' % delta_degree + '°')
            monitor.data.qux = delta_degree
            monitor.data.quux = param.step
            monitor.plot_data[-1] = [param.step, delta_degree]
            await q.page.save()

            await q.sleep(0.5)


@on('#page2')
async def page2(q: Q):
    CartesianResult = 1
    time_consumed = 0
    time_end = 0
    time_start = 0
    cartesianResultNames = ['IK Succeed', 'IK Succeed', 'IK Failed', 'Out of safety Limit',
                            'Time Out']
    # FOR MODE CONTROL

    if q.args['Deactivate']:
        await UDP_CMD.cmd_10.deactiveMode()
        await UDP_CMD.cmd_110.get_work_mode()
        await asyncio.sleep(0.2)
        #await q.page['jointMode0'].save()
    if q.args['PositionMode']:
        await UDP_CMD.cmd_10.initPositionMode()
        await UDP_CMD.cmd_110.get_work_mode()
        await asyncio.sleep(0.2)
        


    await UDP_CMD.cmd_110.get_work_mode()
    # FOR MODE CONTROL
    # 
    # 
    if param.PageFlag != 2:
        param.PageFlag = 2
        await sync_helper(q)
    if q.args['TimeSlider']:
        param.exec_time = q.app['exec_time'] = q.args['TimeSlider']
    if not q.args['realtime_toggle']:
        param.Real_Time_Toggle = False
    else:
        param.Real_Time_Toggle = True

    for i in range(6):
        if q.args[f'cartesianSlider{i}'] is not None:
            param.cartesianTarget[i] = q.app[f'cartesian{i}'] = q.args[f'cartesianSlider{i}']

        if q.args[f'go_zero_button{i}']:
            param.jointTarget[i] = q.app[f'joint{i}'] = 0
            if q.args['realtime_toggle'] is True or q.args['play_button']:
                await q.run(UDP_CMD.cmd_4.Joint_CTRL, param.jointTarget, param.exec_time, param.s4)
        if q.args[f'set_car_button{i}']:
            param.cartesianTarget[i] = q.app[f'cartesian{i}'] = float(q.args[f'cartesianText{i}'])

    if q.args['realtime_toggle'] is True or q.args['play_button']:
        time_start = time.perf_counter()

        CartesianResult = await q.run(UDP_CMD.cmd_6.Cartesian_CTRL, param.cartesianTarget, param.exec_time, param.s6)
        time_end = time.perf_counter()

    if CartesianResult != 1:
        await sync_helper(q)

    time_consumed = time_end - time_start
    if q.args['set_time_button']:
        param.exec_time = float(q.args['time_textbox'])
    if q.args[f'go_home_pos']:
        for i in range(7):
            param.jointTarget[i] = q.app[f'joint{i}'] = 0
        await q.run(UDP_CMD.cmd_4.Joint_CTRL, param.jointTarget, param.exec_time, param.s4)
    jointPage2sync = [q.page['jointGauge0'], q.page['jointGauge1'], q.page['jointGauge2'],
                      q.page['jointGauge3'], q.page['jointGauge4'], q.page['jointGauge5'],
                      q.page['jointGauge6']
                      ]
    cartesianPage2sync = [q.page['cartesianGauge0'], q.page['cartesianGauge1'],
                          q.page['cartesianGauge2'], q.page['cartesianGauge3'], q.page['cartesianGauge4'],
                          q.page['cartesianGauge5'], q.page['cartesianGauge6'], q.page['cartesianGauge7']
                          ]
    future = asyncio.ensure_future(reporter(q, jointPage2sync, cartesianPage2sync))
    # asyncio.ensure_future(sync_helper(q))

    q.page['sidebar'].value = '#page2'
    clear_cards(q)  # When routing, drop all the cards except of the main ones (header, sidebar, meta).
    # curves = 'linear smooth step step-after step-before'.split()
    add_card(q, 'cartesianmonitor', ui.tall_stats_card(
        box=ui.box('sidebar_r', height='280px', width='350px', size=50),
        items=[
            ui.stat(label='IK Result', value=cartesianResultNames[CartesianResult]),
            ui.stat(label='Time', value=(str('%.2f' % (time_consumed * 1000))) + " ms"),
        ]
        # plot_curve='step-after'
    ))
    if param.Monitor_Reporter_ON_Flag is False:
        asyncio.ensure_future(monitorReporter(q, q.page['monitor']))

        
    if q.args['gripperSlider']:
        param.gripperForce  = q.args['gripperSlider']
    if q.args['gripper_calibrate']:
        await UDP_CMD.cmd_7.gripperCalibrate()
    if q.args['gripper_open']:
        await UDP_CMD.cmd_9.gripperCtrl(0,int(param.gripperForce))
    if q.args['gripper_close']:
        await UDP_CMD.cmd_9.gripperCtrl(1,int(param.gripperForce))


    add_card(q, 'joint_ctrl', ui.form_card(box=ui.box('sidebar_r', height='280px', width='350px', size=100), items=[
        ui.text_l(param.Real_Time_Lable[param.Real_Time_Toggle]),
        ui.toggle(name='realtime_toggle', label='Realtime Control', value=param.Real_Time_Toggle, trigger=True),
        ui.separator(label=''),
        ui.text_xl('Control Settings'),
        # ui.text_l('Duration Time (s)' + str(exec_time)),
        ui.slider(name='TimeSlider', label='Duration Time', value=param.exec_time, min=0.5, max=10, step=0.1,
                  trigger=True),
        ui.textbox(name='time_textbox', label='', value='%.1f' % param.exec_time),
        ui.buttons(items=[
            ui.button(name='play_button', label='Play', icon='Next'),
            ui.button(name='set_time_button', label='Set Time', icon='Timer'),

        ]),
        ui.button(name=f'go_home_pos', label='Go Home Position', icon='Home'),
        #ui.button(name=f'add_key_point', label='Add Key Point', icon='EditNote'),

        ui.separator(label=''),
        ui.text_l("Gripper Control"),
        ui.buttons(items=[
            
            ui.button(name='gripper_open', label='Open'),
            ui.button(name='gripper_close', label='Close'),
            ui.button(name='gripper_calibrate', label='Calibrate'),
        ]),
        ui.slider(name='gripperSlider', label='Force', value=param.gripperForce, min=2, max=20, step=1,
          trigger=True),

    ]))

    for i in range(7):
        add_card(q, f'jointGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
            title=f'Joint {i + 1}',
            value=str('%.2f' % (param.jointNow[i] * 57.29)) + '°',
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#2196f3',
            progress=1 - ((param.jointNow[i] * 57.29 + 145) / 290)
        ))
    multiline_content = str(
        f"6,40,114514,{round(param.cartesianTarget[0] / 1000, 2)},{round(param.cartesianTarget[1] / 1000, 3)},{round(param.cartesianTarget[2] / 1000, 3)},{round(param.cartesianTarget[3] / 57.29, 3)},{round(param.cartesianTarget[4] / 57.29, 2)},{round(param.cartesianTarget[5] / 57.29, 2)},0,{param.exec_time}")
    #add_card(q, f'copy', ui.form_card(
    #    box=ui.box(f'horizontalL0', height='150px', width='150px', size='0'),
    #    items=[ui.copyable_text(label='Copyable UDP Protocol Payload', value=multiline_content,
    #                            multiline=True, height='100px')]
    #))
    #add_card(q, f'Placeholder', ui.large_stat_card(
    #    box=ui.box(f'horizontalL0', height='150px', width='150px', size=1),
    #    title='Model',
    #    value='Amber Lucid ONE',
    #    aux_value='',
    #    data=dict(qux=845, quux=0.8),
    #    caption='',
    #))
    add_card(q, f'jointMode0',ui.form_card(box=ui.box(f'horizontalL0', height='150px', width='280px', size=1), items=[
        ui.text_xl('Actuator Mode'),
        ui.facepile(max=9,items=[ 
        ui.persona(title=param.jointModeNames[param.jointMode[0]]),
        ui.persona(title=param.jointModeNames[param.jointMode[1]]),
        ui.persona(title=param.jointModeNames[param.jointMode[2]]),
        ui.persona(title=param.jointModeNames[param.jointMode[3]]),
        ui.persona(title=param.jointModeNames[param.jointMode[4]]),
        ui.persona(title=param.jointModeNames[param.jointMode[5]]),
        ui.persona(title=param.jointModeNames[param.jointMode[6]]),
    ]),ui.buttons( items=[ui.button(name='PositionMode', label='PositionMode', primary=True,icon = 'Warning'),
    ui.button(name='Deactivate', label='Deactivate',icon = 'Warning')])

    ]))
    for i in range(2):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL{i + 1}', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value=str('%.2f' % (param.cartesianNow[i] * 1000)),
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(625 + float('%.2f' % (param.cartesianNow[i] * 1000))) / 931.2)
                 )
        add_card(q, f'cartesianForm{i}',
                 ui.form_card(box=ui.box(f'horizontalL{i + 1}', height='150px', width='350px', size=1), items=[
                     ui.slider(name=f'cartesianSlider{i}', label=param.cartesianNames[i],
                               value=param.cartesianTarget[i], min=-587.5,
                               max=587.5,
                               step=0.1, trigger=True),
                     ui.inline(
                         items=[ui.textbox(name=f'cartesianText{i}', label='', value='%.2f' % param.cartesianTarget[i]),
                                ui.buttons(justify='end', items=[
                                    # ui.button(name=f'go_zero_button{i}', label='Go Zero', icon='Reset'),
                                    ui.button(name=f'set_car_button{i}', label='Update', icon='ChevronUpMed')])
                                ])
                 ]))
    for i in range(2, 3):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL{i + 1}', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value=str('%.2f' % (param.cartesianNow[i] * 1000)),
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(300 + round(param.cartesianNow[i] * 1000, 2)) / 806.1)
                 )
        add_card(q, f'cartesianForm{i}',
                 ui.form_card(box=ui.box(f'horizontalL{i + 1}', height='150px', width='350px', size=1), items=[
                     ui.slider(name=f'cartesianSlider{i}', label=param.cartesianNames[i],
                               value=param.cartesianTarget[i], min=-377.7,
                               max=755.30,
                               step=0.1, trigger=True),
                     ui.inline(
                         items=[ui.textbox(name=f'cartesianText{i}', label='', value='%.2f' % param.cartesianTarget[i]),
                                ui.buttons(justify='end', items=[
                                    # ui.button(name=f'go_zero_button{i}', label='Go Zero', icon='Reset'),
                                    ui.button(name=f'set_car_button{i}', label='Update', icon='ChevronUpMed')])
                                ])]))
    for i in range(3, 6):
        add_card(q, f'cartesianGauge{i}', ui.tall_gauge_stat_card(
            box=ui.box(f'horizontalL{i + 1}', height='150px', width='150px', size='0'),
            title=param.cartesianNames[i],
            value=str('%.2f' % (param.cartesianNow[i] * 57.29) + "°"),
            # aux_value='target: ' + str(param.jointTarget[i]) + '°',
            aux_value='',
            plot_color='#f44336',
            progress=(180 + float('%.2f' % (param.cartesianNow[i] * 57.29))) / 360
        ))
        add_card(q, f'cartesianForm{i}',
                 ui.form_card(box=ui.box(f'horizontalL{i + 1}', height='150px', width='350px', size=1), items=[
                     ui.slider(name=f'cartesianSlider{i}', label=param.cartesianNames[i],
                               value=round(param.cartesianTarget[i], 2), min=-180,
                               max=180,
                               step=0.1, trigger=True),
                     ui.inline(
                         items=[ui.textbox(name=f'cartesianText{i}', label='', value='%.2f' % param.cartesianTarget[i]),
                                ui.buttons(justify='end', items=[
                                    # ui.button(name=f'go_zero_button{i}', label='Go Zero', icon='Reset'),
                                    ui.button(name=f'set_car_button{i}', label='Update', icon='ChevronUpMed')])
                                ])
                 ]))

async def recorder(q: Q):

    if (param.isRecording):
        return
    if (param.isReplaying):
        return
    else:
        param.isRecording = True
        param.record_list = []
        await UDP_CMD.cmd_10.currentMode()
        from lcmTypes.armStatus_t import armStatus_t
        
        def my_handler(channel, data):
            msg = armStatus_t.decode(data)
            print("Received message on channel \"%s\"" % channel)
            
            tmp = [msg.jointPosition[0],msg.jointPosition[1],msg.jointPosition[2],msg.jointPosition[3],msg.jointPosition[4],msg.jointPosition[5],msg.jointPosition[6]]
            print("   position    = %s" % str(tmp))
            param.record_list.append(tmp)
        lc = lcm.LCM()
        subscription = lc.subscribe(lcm_recv_channel, my_handler)
        
        
        while True:
            if(param.isRecording == False):
                await UDP_CMD.cmd_10.initPositionMode()
                return
            lc.handle()
            await q.sleep(0.05)


async def replayer(q: Q,controlTable):
    if (param.isRecording):
        return
    if (param.isReplaying):
        return
    else:
        param.isReplaying = True
        from lcmTypes.posCmd_t import posCmd_t
        for item in param.record_list:
            if(param.isReplaying == False):
                return
            msg = posCmd_t()
            msg.jointTarget = (item[0],item[1],item[2],item[3],item[4],item[5],item[6])
            print(msg.jointTarget)
            lc = lcm.LCM()
            lc.publish(lcm_send_channel, msg.encode())
            await q.sleep(0.05)
        controlTable.items=[
            ui.text_xl('🟢StandBy'),
            ui.buttons([
            ui.button(name='RecorderOnSwitch', label='Start Recording', caption='Enable Drag Mode'),
            ui.button(name='StopSwitch', label='Stop', caption='Enable Position Mode'),
            ui.button(name='ReplaySwitch', label='Start Replay', caption='Enable Position Mode')]),
            ui.textbox(name='filenameTextBox', label='File Name', 
                      value=param.readyFilename,suffix='.csv'),
            ui.buttons(justify='end', items=[ui.button(name='SaveFile', label='Save File')])


        ]
        param.isReplaying = False
        await q.page.save()
import glob
import csv
@on('#page3')
async def page3(q: Q):
    if q.args['SaveFile']:
        param.readyFilename = q.args['filenameTextBox']
        print(q.args['filenameTextBox'])
        print(param.readyFilename)


        if (len(param.record_list)> 1) :
            print("savefile")
            with open('./RecordedFiles/'+param.readyFilename+'.csv', 'w') as f:
                writer = csv.writer(f)
                writer.writerows(param.record_list)
            await q.sleep(0.1)
    mylist = [f for f in glob.glob("./RecordedFiles/*.csv")]
    param.readyFilename = "drag-"+datetime.now().strftime('%y-%m-%d-%H-%M-%S')
    workingMode = 'StandBy'
    tableRow = []

    for i in range (len(mylist)):
        tableRow.append(ui.table_row(name=f'{i}',  cells=[mylist[i]]))


    q.page['sidebar'].value = '#page3'


    if q.args['RecorderOnSwitch']:
        asyncio.ensure_future(recorder(q))
        await asyncio.sleep(0.1)

    if q.args['ReplaySwitch']:
        asyncio.ensure_future(replayer(q,q.page['controlTable']))
        await asyncio.sleep(0.1)


    if q.args['file_table']:
        j = int(q.args['file_table'][0])
        with open(mylist[j], newline='') as f:
            reader = csv.reader(f)
            csvData = list(reader)
            #print("?????"+f'row{i}')
            print(csvData)
            yi = 0
            xi = 0
            for x in csvData:
                
                for y in x:
                    csvData[xi][yi] = float(y)
                    #print(y)
                    yi+=1
                yi = 0
                xi+=1

            param.record_list = csvData

    if q.args['StopSwitch']:
        param.isRecording = False
        param.isReplaying = False


    if (param.isRecording):
        workingMode = '⏺️Recording'

    elif (param.isReplaying):
        workingMode = '▶️Replaying'
    else:
        workingMode = '🟢StandBy'


    

    clear_cards(q)  # When routing, drop all the cards except of the main ones (header, sidebar, meta).
    

    add_card(q, 'controlTable',ui.form_card(box=ui.box(f'horizontalL0',width='512px',size='0'), items=[
            ui.text_xl(workingMode),
            ui.buttons([
            ui.button(name='RecorderOnSwitch', label='Start Recording', caption='Enable Drag Mode'),
            ui.button(name='StopSwitch', label='Stop', caption='Enable Position Mode'),
            ui.button(name='ReplaySwitch', label='Start Replay', caption='Enable Position Mode')]),
            ui.textbox(name='filenameTextBox', label='File Name', 
                      value=param.readyFilename,suffix='.csv'),
            ui.buttons(justify='end', items=[ui.button(name='SaveFile', label='Save File')])


        ]
        ))

    add_card(q, 'fileTable',ui.form_card
        (box=ui.box(f'horizontalL0',width='1024px',height='512px',size='0'),
        items=[ui.table(
        name='file_table',height='480px',
        columns=[ui.table_column(name='name', label='Name',cell_overflow='wrap', searchable=True)],
        rows=tableRow,
    )
       ]))




async def init(q: Q) -> None:
    q.page['meta'] = ui.meta_card(box='', title='Dashboard', icon=img.A.img128px,
                                  themes=[
                                      ui.theme(
                                          name='amber-dark-mode',
                                          primary='#ffffff',
                                          text='#e8e1e1',
                                          card='#22272e',
                                          page='#070b1a',
                                      )
                                  ], theme=param.theme,
                                  layouts=[ui.layout(breakpoint='xs', min_height='100vh', zones=[
                                      ui.zone('main', size='1', direction=ui.ZoneDirection.ROW, zones=[
                                          ui.zone('sidebar', size='200px'),
                                          ui.zone('body', zones=[
                                              ui.zone('header'),
                                              ui.zone('content_master', direction=ui.ZoneDirection.ROW, zones=[

                                                  ui.zone('content', wrap='stretch',
                                                          justify='center', zones=[
                                                          # Specify various zones and use the one that is currently needed. Empty zones are ignored.
                                                          ui.zone('contents', direction=ui.ZoneDirection.ROW,
                                                                  wrap='stretch',
                                                                  justify='center', zones=[
                                                                  ui.zone('contentsL',
                                                                          wrap='stretch',
                                                                          justify='center',
                                                                          zones=[ui.zone('horizontalL0',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL1',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL2',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL3',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL4',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL5',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL6',
                                                                                         direction=ui.ZoneDirection.ROW),
                                                                                 ui.zone('horizontalL7',
                                                                                         direction=ui.ZoneDirection.ROW), ]),

                                                              ])]),

                                                  ui.zone('sidebar_r', size='350px'),

                                              ]),
                                          ]),

                                      ])
                                  ])])
    q.page['sidebar'] = ui.nav_card(
        box='sidebar', color='card', title='Amber Dashboard', subtitle="Driving the world",
        value=f'#{q.args["#"]}' if q.args['#'] else '#page1',
        image=img.LOGO.img, items=[
            ui.nav_group('Menu', items=[
                ui.nav_item(name='#page1', label='Joint Control'),
                ui.nav_item(name='#page2', label='Cartesian Control'),
                ui.nav_item(name='#page3', label='Dragging to Teach'),
                # ui.nav_item(name='#page4', label='Form'),
            ]),
        ])
    param.HostIP = UDP_CMD.get_ip.get_host_ip()
    q.page['header'] = ui.header_card(
        box=ui.box('header', height='80px'), title='', subtitle='', color='card',
        # secondary_items=[ui.textbox(name='search', icon='Search', width='400px', placeholder='Search...')],
        items=[
            ui.persona(title='Online', subtitle='ROS Status', size='s',
                       image=img.PowerButton.online
                       )], )
    # secondary_items=[
    # ui.button(name='Mode', label=param.controlModeNames[param.jointControl], primary=True, icon='CubeShape'),
    #   ui.button(name='3D', label='3D Visualization', primary=True, icon='CubeShape',
    #            path=f'http://{param.HostIP}:7000/static/')])

    asyncio.ensure_future(ROS_status_reporter(q, q.page['header']))
    #asyncio.ensure_future(reporter(q, jointPage2sync, cartesianPage2sync))
    # If no active hash present, render page1.
    if q.args['#'] is None:
        await page1(q)


# async def meshcat_bridge_guard(q: Q):
#    if param.Meshcat_ON_Flag is False:
#        param.Meshcat_ON_Flag = True
#        while True:
#            await UDP_CMD.meshcat_bridge.push_status()
#            await q.sleep(0.1)


async def ROS_status_reporter(q: Q, p):
    if param.ROS_status_reporter_ON_Flag is False:
        param.ROS_status_reporter_ON_Flag = True
        param.s1.bind(("0.0.0.0", 12321))
        param.s4.bind(("0.0.0.0", 12324))
        param.s6.bind(("0.0.0.0", 12326))
        #param.s110.bind(("0.0.0.0", 12430))
        while True:
            await UDP_CMD.cmd_1.get_status(param.s1)
            await UDP_CMD.cmd_110.get_work_mode()
            #await UDP_CMD.cmd_110.get_work_mode(param.s110)
            if param.ROS_Online_Flag and param.ROS_Power_button:
                p.items = [ui.persona(title='Online   🟢', subtitle=param.HostIP, size='s',
                                      image=img.PowerButton.online
                                      ), ]
                param.ROS_Power_button = not param.ROS_Power_button
            elif param.ROS_Online_Flag:
                p.items = [ui.persona(title='Online   ⚪', subtitle=param.HostIP, size='s',
                                      # image=img.PowerButton.onlineB
                                      image=img.PowerButton.online
                                      ), ]
                param.ROS_Power_button = not param.ROS_Power_button
            else:
                p.items = [ui.persona(title='Offline  🔴', subtitle=param.HostIP, size='s',
                                      image=img.PowerButton.offline
                                      ), ]
            # p.secondary_items = [
            #    #ui.button(name='Mode', label=param.controlModeNames[param.jointControl], primary=True,
            #    #          icon='CubeShape'),
            #    ui.button(name='3D', label='3D Visualization',  icon='CubeShape',
            #              path=f'http://{param.HostIP}:7000/static/')
            #    ]
            await q.page.save()
            await q.sleep(0.5)


@app('/', mode='broadcast')
async def serve(q: Q):
    # Run only once per client connection.
    if not q.client.initialized:
        q.client.cards = set()
        await init(q)
        q.client.initialized = True

    # Handle routing.
    await handle_on(q)
    await q.page.save()
