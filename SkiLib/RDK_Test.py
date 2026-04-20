# Type help("robodk.robolink") or help("robodk.robomath") for more information
# Press F5 to run the script
# Documentation: https://robodk.com/doc/en/RoboDK-API.html
# Reference:     https://robodk.com/doc/en/PythonAPI/robodk.html
# Note: It is not required to keep a copy of this file, your Python script is saved with your RDK project

# You can also use the new version of the API:
from robodk import robolink    # RoboDK API
from robodk import robomath

from SkiLib.robotcontext import RobotContext
from SkiLib.sensors.gripper import _query_attachment    # Robot toolbox
RDK = robolink.Robolink()

# Forward and backwards compatible use of the RoboDK API:
# Remove these 2 lines to follow python programming guidelines
from robodk import *      # RoboDK API
from robolink import *    # Robot toolbox
# Link to RoboDK
from SkiLib.log import get_logger
logger = get_logger(__name__) 

def get_Robot_Type():
    # Retrieve the robot item. Passing an empty string and the item type 
    # will get the first available robot in the station.
    robot = RDK.Item('', ITEM_TYPE_ROBOT)

    if not robot.Valid():
        print("No robot found in the station.")
        return None

    # Get the name of the robot
    robot_name = robot.Name()
    print(f"Robot Name/Type: {robot_name}")
    return robot_name

def get_Tool_Name():
    '''
    This fucntion returns the tool name that is attached to the robot
    
    Input Arguements: None
    
    Returns: String - the name of the tool attached to the robot as named in the RoboDK tree
    
    '''
    robot = RDK.Item('', ITEM_TYPE_ROBOT)
    
    tool = robot.getLink(ITEM_TYPE_TOOL)

    if tool.Valid():
        tool_name = tool.Name()
        print(f"Attached Tool: {tool_name}")
        return tool_name
    else:
        print("No specific tool attached (using default flange TCP).")
        return None

def run_Predefined_Programs(progName, wait_for_finish):
    '''
    This function is used to execute local programs within RoboDK
    
    Input Arguements:
    prgName(String) : Name of the program as in the RoboDK tree structure
    wait_for_finsih(boolean) : True - will wait for the program to finish, False -Does not wait and moves to next line for execution
    
    Return: None  
    '''
    RDK.RunProgram(progName, wait_for_finish)
    
    print("Completed")

def read_Robot_Pose():
    ''' 
    This function is used to read the robot pose.
    
    Input aruguements : None
    
    Returns : list[float] ([X,Y,Z, u,v,w] representation (position in mm and orientation vector in radians))

    '''
    robot  = RDK.Item('', ITEM_TYPE_ROBOT)  # Retrieve a robot available in RoboDK
    pose = robot.Pose()                     # retrieve the current robot position as a pose (position of the active tool with respect to the active reference frame)

    # Read the 4x4 pose matrix as [X,Y,Z, u,v,w] representation (position in mm and orientation vector in radians): same representation as Universal Robots
    xyzuvw = Pose_2_UR(pose)
    return xyzuvw

def get_Frame_pose(frame):
    ''' 
    This function is used to get the pose of any frame in the RoboDK tree.
    
    Input aruguements :
    frame(String) - Name of the frame as present in the RoboDK tree
    
    Returns : list[float] ([X,Y,Z, u,v,w] representation (position in mm and orientation vector in radians))

    '''

    frame = RDK.Item(str(frame), robolink.ITEM_TYPE_FRAME)

    if frame.Valid():
        # Get the pose relative to its parent frame
        # Returns a Mat object (4x4 matrix)
        pose = frame.Pose()

        # Convert matrix to human-readable coordinates (X, Y, Z, W, P, R)
        # Default is the RoboDK format: [x, y, z, rx, ry, rz] in mm and degrees
        xyzrpw = robomath.Pose_2_TxyzRxyz(pose)
        # print("\nCartesian coordinates (mm, deg):")
        # print(xyzrpw)
        return xyzrpw
    
    else:
        print("Frame not found!")
        return None

def set_Frame_Pose(frame,new_pose):
    ''' 
    This function is used to change the existing frame location to a new location.  
    
    Input arguements: 
    frame (String) - the name of the frame that needs to be moved.
    new_pose (list[float]) - ([X,Y,Z, u,v,w] representation (position in mm and orientation vector in radians))
    
    Return type: 
    '''

    frame = RDK.Item(str(frame), robolink.ITEM_TYPE_FRAME)

    if frame.Valid():
        # Define new coordinates: [X, Y, Z, RX, RY, RZ]
        # Units: mm and degrees
        
        
        # Convert the list to a 4x4 Matrix
        new_pose_changed = robomath.TxyzRxyz_2_Pose(new_pose)
        
        # Apply the pose to the frame
        frame.setPose(new_pose_changed)
        print("Frame moved successfully.")

def individual_Joint_Move(joint_index,degree,relative =False):
    ''' 
    This function is used to move specific joint by providing a joint angle in degrees.  
    
    Input arguements: 
    joint_index (int) - the index indicates which specific joint that needs to be moved.
    degree (float) - the degree which the particular joint is to be moved
    relative (boolean) - True: moves the joint relative in degrees to its existing pose, False: moce the joint to the defined postions in degrees 
    
    Return type: 
    '''
    robot = RDK.Item('', ITEM_TYPE_ROBOT)
    # Get current joint values (returns a list of floats)
    joints = robot.Joints().list()

    if not relative:
        # Note: Python uses 0-based indexing (Joint 1 = index 0, Joint 3 = index 2)
        joints[int(joint_index)] = float(degree) 

        # Set the robot to the new joint configuration
        # This updates the simulation instantly
        robot.setJoints(joints)

        # (Optional) Make the robot move smoothly to that joint position
        #robot.MoveJ(joints)
    else:
        joints[int(joint_index)] += float(degree)

        # Set the robot to the new joint configuration
        # This updates the simulation instantly
        robot.setJoints(joints)

        # (Optional) Make the robot move smoothly to that joint position
        #robot.MoveJ(joints)

def moveJ(start_pose,end_pose,velocity,acceleration):
    ''' 
    This function is used to move the robot TCP from one pose to another.
    '''
    
    
    pass
    
def moveL(start_pose,end_pose,velocity,acceleration):
    pass

def check_linear_path(robot, target_pose, restore_position=True):
    '''
    检查线性路径是否可行，不改变机器人的当前位置（可选）
    
    Input Arguments:
    robot (Item) : 机器人对象
    target_pose (Mat or Item) : 目标位姿或目标对象
    restore_position (bool) : True-检查后恢复原位置, False-允许移动到碰撞点或目标点
    
    Returns:
    int : 0=无碰撞, -1=无法线性运动, -2=目标不可达, >0=碰撞对象数
    '''
    # 保存当前关节位置
    current_joints = robot.Joints()
    
    # 获取目标位姿
    if isinstance(target_pose, Item):
        target_pose = target_pose.Pose()
    
    # 执行路径检查
    result = robot.MoveL_Test(current_joints, target_pose)
    
    # 恢复到原始位置（如果需要）
    if restore_position:
        robot.setJoints(current_joints)
    
    # 返回检查结果
    return result

def check_joint_path(robot, target_joints, restore_position=True):
    '''
    检查关节运动路径是否可行，不改变机器人的当前位置（可选）
    
    Input Arguments:
    robot (Item) : 机器人对象
    target_joints (list or Mat or Item) : 目标关节位置或目标对象
    restore_position (bool) : True-检查后恢复原位置, False-允许移动到碰撞点或目标点
    
    Returns:
    int : 0=无碰撞, >0=碰撞对象数
    '''
    # 保存当前关节位置
    current_joints = robot.Joints()
    
    # 获取目标关节位置
    if isinstance(target_joints, Item):
        target_joints = target_joints.Joints()
    
    # 执行路径检查
    result = robot.MoveJ_Test(current_joints, target_joints)
    
    # 恢复到原始位置（如果需要）
    if restore_position:
        robot.setJoints(current_joints)
    
    # 返回检查结果
    return result

def validate_path_quick(robot_or_rdk, target, move_type='linear'):
    '''
    使用快速验证模式检查路径（推荐方法，不会改变机器人位置）
    
    Input Arguments:
    robot_or_rdk (Item or Robolink) : 机器人对象或RDK对象
    target (Item or Mat or list) : 目标对象、位姿或关节位置
    move_type (str) : 'linear' 或 'joint'
    
    Returns:
    bool : True=路径可行, False=路径有问题
    '''
    # 获取 RDK 和 robot 对象
    if isinstance(robot_or_rdk, Item):
        robot = robot_or_rdk
        rdk = robot.RDK()
    else:
        rdk = robot_or_rdk
        robot = rdk.Item('', ITEM_TYPE_ROBOT)
    
    # 保存当前运行模式
    original_mode = rdk.RunMode()
    
    try:
        # 设置为快速验证模式
        rdk.setRunMode(RUNMODE_QUICKVALIDATE)
        
        # 执行移动（只验证不实际移动）
        if move_type.lower() == 'linear':
            robot.MoveL(target)
        else:
            robot.MoveJ(target)
        
        # 验证成功
        return True
        
    except Exception as e:
        # 验证失败
        print(f"路径验证失败: {e}")
        return False
        
    finally:
        # 恢复原来的运行模式
        rdk.setRunMode(original_mode)

def test_moveJ():
    pass

def test_moveL():
    '''
    测试线性运动路径检查功能
    '''
    robot = RDK.Item('', ITEM_TYPE_ROBOT)
    target = RDK.Item("App Pick Part A", robolink.ITEM_TYPE_TARGET)
    
    print("\n=== 方法1: 使用 MoveL_Test (会暂时移动机器人) ===")
    result = check_linear_path(robot, target, restore_position=True)
    if result == 0:
        print("✓ 路径检查通过，无碰撞")
    elif result == -1:
        print("✗ 目标可达但无法线性运动")
    elif result == -2:
        print("✗ 目标位姿不可达")
    else:
        print(f"✗ 检测到 {result} 对碰撞对象")
    
    print("\n=== 方法2: 使用快速验证模式 (推荐) ===")
    if validate_path_quick(robot, target, 'linear'):
        print("✓ 路径验证通过")
    else:
        print("✗ 路径验证失败")
        

def reset_station():
    '''
    这个函数可以用来重置RoboDK场景到初始状态，适用于测试前的准备工作。
    你需要在RoboDK中创建一个程序（例如“Reset Station”），包含所有必要的步骤来重置场景。
    '''
    RDK.RunProgram("Reset Parts", wait_for_finished=True)
    logger.info("Station reset completed.")
    print("Station reset completed.")

if __name__=="__main__":
    robot = RDK.Item('', ITEM_TYPE_ROBOT)
    ctx = RobotContext()
    get_Robot_Type()
    get_Tool_Name()
    
    frame_partA = RDK.Item(str("Part_A_1 Frame"), robolink.ITEM_TYPE_FRAME)
    start_partA = RDK.Item("Home A", robolink.ITEM_TYPE_TARGET)
    target_partA = RDK.Item("App Pick Part A", robolink.ITEM_TYPE_TARGET)
    print(_query_attachment("Gripper Extension"))
    
    # robot.setSpeed(speed_linear=-1, speed_joints=4)
    # RDK.setCollisionActive(False) # Bypass collision testing
    # # Remember to properly setup collision pairs.
    # print(robot.MoveJ_Test(robot.Joints(), target_partA.Joints()))
    # robot.MoveL(target_partA)
    # print(robot.Joints())
    # #     robot.MoveJ(start_partA)
    # print(target_partA.Joints())
    
    # robot.MoveL()
    
    # #prefixed sequence run from local sub-routine programs on RoboDK 
    # # run_Predefined_Programs('Reset Parts',False)
    # # run_Predefined_Programs('Pick&Place Part A',True)
    # # run_Predefined_Programs('Pick&Place Part B',True)
    # # run_Predefined_Programs('Rotate Combined Part',True)
    # # run_Predefined_Programs('Pick&Place Part C',True)
    # # run_Predefined_Programs('Reset Parts',False)
    


