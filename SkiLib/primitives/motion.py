from SkiLib.base import BasePrimitive, CheckResult, CheckResultLevel
from robodk import robolink    # RoboDK API
from robodk import robomath    # Robot toolbox
from robodk.robolink import Item
from typing import Union, List

class MoveJ(BasePrimitive):
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item = robot_object
        self.RDK: robolink.Robolink = RDK_object
    

    def check(self, target: Union['Item', List[float], robomath.Mat], ref_frame: robomath.Mat=None) -> CheckResult:
        
        
        start = self.robot.Joints()
        # ==================== Handle target inputs (check only takes joints) ====================
        _target = None
        if isinstance(target, Item):
            _target = target.Joints()
        elif isinstance(target, list):
            _target = target
        elif isinstance(target, robomath.Mat):
            if ref_frame is None:
                # Type check, a pose must come with a reference frame.
                return CheckResult(
                    is_valid=False, 
                    message="A pose target must be accompanied by a reference frame.",
                    category="input_validation",
                    level=CheckResultLevel.ERROR,
                    suggestion="Provide a reference frame when using pose targets."
                )
            else:
                # We can do some additional checks here, like checking if the target pose is within the robot's reachability or if the reference frame is valid.
                _target = list(self.robot.SolveIK(pose=target, reference=ref_frame))
                if len(_target) == 0:
                    return CheckResult(
                        is_valid=False, 
                        message="Target pose does not have a valid IK solution.",
                        category="ik_solution",
                        level=CheckResultLevel.ERROR,
                        suggestion="Check if the target pose is within the robot's reachable workspace and verify the pose orientation is achievable."
                    )
                
        else:
                return CheckResult(
                    is_valid=False, 
                    message="Invalid target type. Target must be an Item, a list of joint values, or a pose with a reference frame.",
                    category="input_validation",
                    level=CheckResultLevel.ERROR,
                    suggestion="Provide a valid target: Item, list of joint values, or Mat pose with reference frame."
                )
        # MoveJ_Test only accepts joints.
        # ==================== End of target handling ====================
        self.RDK.setCollisionActive(True) # Enable collision checking for the test 
        test_result = self.robot.MoveJ_Test(start,_target)
        self.RDK.setCollisionActive(False) # Disable collision checking for the test TODO Verify if this can cause any issue. We want to make sure to not affect any subsequent movement that might rely on the collision state. Maybe we can move this line to the beginning of the check function to make sure every check will have collision checking enabled, and we can remove the line in execute function since check will always be called before execute.
        if test_result == 0:
            return CheckResult(
                is_valid=True, 
                message="Path is valid and collision-free.",
                category="collision",
                level=CheckResultLevel.INFO
            )
        else:
            return CheckResult(
                is_valid=False, 
                message=f"Path is not valid. Motion would cause collisions in the station.",
                category="collision",
                level=CheckResultLevel.ERROR,
                details={
                    "collision_count": test_result,
                },
                suggestion="This count includes all collisions in the station, not just those on the path. Some may be external or implicitly caused by this move.Check the collision map to identify all collision pairs and adjust the path or robot configuration to avoid them."
            )
    

    def execute(self, target: Union['Item', List[float], robomath.Mat], blocking: bool = True, ref_frame: robomath.Mat=None) -> robomath.Mat:        
        if isinstance(target, robomath.Mat):
            if ref_frame is None:
                # Type check, a pose must come with a reference frame.
                raise ValueError("A pose target must be accompanied by a reference frame.")
            else:
                prev_frame = self.robot.PoseFrame()
                self.robot.setPoseFrame(ref_frame)
                try: # In case of any error during movement, we want to make sure to restore the previous reference frame.
                    self.robot.MoveJ(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(prev_frame)
                return self.robot.Pose()
        # other than only Pose, we can execute MoveJ directly.    
        else:
            self.robot.MoveJ(target, blocking=blocking)
        return self.robot.Joints()
    

    def try_execute(self, target: Union['Item', List[float], robomath.Mat], start: List[float] = None, ref_frame: robomath.Mat=None, blocking: bool = True) -> tuple[CheckResult, robomath.Mat]:
        # Perform the check, if valid proceed to execute, return a successful check and final position. Otherwise a failed check and original position.
        check_result = self.check(target, start, ref_frame)
        if check_result.is_valid:
            final_joints = self.execute(target, blocking, ref_frame)
            return CheckResult(True, "Move executed successfully."), final_joints
        else:
            return check_result, self.robot.Joints()
    


class moveL(BasePrimitive):
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item = robot_object
        self.RDK: robolink.Robolink = RDK_object
    

    def check(self, target: Union['Item', List[float], robomath.Mat], ref_frame: robomath.Mat=None):
        _start = self.robot.Joints() # MoveL_Test accepts joints as start point parameter.
        # Movel_Test onl accepts pose as target.
    

    def execute(self, target: Union['Item', List[float], robomath.Mat], ref_frame: robomath.Mat=None,blocking: bool = True):
        if isinstance(target, robomath.Mat):
            if ref_frame is None:
                # Type check, a pose must come with a reference frame.
                raise ValueError("A pose target must be accompanied by a reference frame.")
            else:
                prev_frame = self.robot.PoseFrame()
                self.robot.setPoseFrame(ref_frame)
                try: # In case of any error during movement, we want to make sure to restore the previous reference frame.
                    self.robot.MoveL(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(prev_frame)
                return self.robot.Pose()
        # other than only Pose, we can execute MoveL directly.    
        else:
            self.robot.MoveL(target, blocking=blocking)
        return self.robot.Pose()
    

    def try_execute(self, *args, **kwargs):
        pass

