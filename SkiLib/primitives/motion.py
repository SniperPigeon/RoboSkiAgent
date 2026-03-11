from SkiLib.base import (
    BasePrimitive, SkillResult, ExecutionPhase, RobotState,
    ERROR_INVALID_PARAM, ERROR_MISSING_REF_FRAME,
    ERROR_IK_FAILURE, ERROR_COLLISION, ERROR_TIMEOUT, require_robot_active,
)
from robodk import robolink
from robodk import robomath
from robodk.robolink import Item
from typing import Optional, Union, List


def _snapshot(robot: robolink.Item) -> RobotState:
    """Capture current robot state. Returns RobotState with None fields if unreachable."""
    try:
        return RobotState(joints=list(robot.Joints()), pose=robot.Pose())
    except Exception:
        return RobotState()


class MoveJ(BasePrimitive):
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object

    def check(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None ) -> SkillResult: #
        start = self.robot.Joints()

        # Resolve target to joint values for MoveJ_Test
        if isinstance(target, Item):
            _target = target.Joints()
        elif isinstance(target, list):
            _target = target
        elif isinstance(target, robomath.Mat):
            if ref_frame is None:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_MISSING_REF_FRAME,
                    message="A pose target must be accompanied by a reference frame.",
                    suggestion="Provide a reference frame when using pose targets.",
                )
            _target = list(self.robot.SolveIK(pose=target, reference=ref_frame))
            if len(_target) == 0:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.PLANNING,
                    error_type=ERROR_IK_FAILURE,
                    message="Target pose does not have a valid IK solution.",
                    suggestion="Check if the target pose is within the robot's reachable workspace and verify the pose orientation is achievable.",
                )
        else:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid target type. Target must be an Item, a list of joint values, or a Mat pose.",
                suggestion="Provide a valid target: Item, list of joint values, or Mat pose with reference frame.",
            )

        # Collision pre-check
        self.RDK.setCollisionActive(True)
        test_result = self.robot.MoveJ_Test(start, _target)
        self.RDK.setCollisionActive(False)

        if test_result == 0:
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.PLANNING,
                message="Path is valid and collision-free.",
            )
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.PLANNING,
            error_type=ERROR_COLLISION,
            message="Path would cause collisions in the station.",
            data={"collision_count": test_result},
            suggestion=(
                "This count includes all collisions in the station, not just those on the path. "
                "Some may be external or implicitly caused by this move. "
                "Check the collision map to identify all collision pairs and adjust the path or robot configuration."
            ),
        )
    @require_robot_active
    def execute(self, target: Union[Item, List[float], robomath.Mat], blocking: bool = True, ref_frame: Optional[robomath.Mat] = None) -> SkillResult:
        try:
            if isinstance(target, robomath.Mat):
                if ref_frame is None:
                    return SkillResult(
                        success=False,
                        execution_phase=ExecutionPhase.VALIDATION,
                        error_type=ERROR_MISSING_REF_FRAME,
                        message="A pose target must be accompanied by a reference frame.",
                        suggestion="Provide a reference frame when using pose targets.",
                    )
                prev_frame = self.robot.PoseFrame()
                self.robot.setPoseFrame(ref_frame)
                try:
                    self.robot.MoveJ(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(prev_frame)
            else:
                self.robot.MoveJ(target, blocking=blocking)

            state = _snapshot(self.robot)
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message="MoveJ executed successfully.",
                data={"joints": state.joints},
            )
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                message=f"MoveJ failed during execution: {type(e).__name__}",
                robot_state=_snapshot(self.robot),
                suggestion="Check the robot connection and station state.",
            )

    def try_execute(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None, blocking: bool = True) -> SkillResult:
        check = self.check(target, ref_frame)
        if not check.success:
            return check
        return self.execute(target, blocking, ref_frame) #type: ignore


class MoveL(BasePrimitive):
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object
        
    def check(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None) -> SkillResult:
        # TODO: implement MoveL_Test pre-flight check (known pending item per CLAUDE.md)
        return SkillResult(
            success=True,
            execution_phase=ExecutionPhase.PLANNING,
            message="MoveL pre-flight check not yet implemented; skipping.",
        )
    @require_robot_active(bypass_halt=False)
    def execute(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None, blocking: bool = True) -> SkillResult:
        try:
            if isinstance(target, robomath.Mat):
                if ref_frame is None:
                    return SkillResult(
                        success=False,
                        execution_phase=ExecutionPhase.VALIDATION,
                        error_type=ERROR_MISSING_REF_FRAME,
                        message="A pose target must be accompanied by a reference frame.",
                        suggestion="Provide a reference frame when using pose targets.",
                    )
                prev_frame = self.robot.PoseFrame()
                self.robot.setPoseFrame(ref_frame)
                try:
                    self.robot.MoveL(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(prev_frame)
            else:
                self.robot.MoveL(target, blocking=blocking)

            state = _snapshot(self.robot)
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.EXECUTION,
                robot_state=state,
                message="MoveL executed successfully.",
                data={"pose": state.pose},
            )
        except Exception as e:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                message=f"MoveL failed during execution: {type(e).__name__}",
                robot_state=_snapshot(self.robot),
                suggestion="Check the robot connection and station state.",
            )

    def try_execute(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None, blocking: bool = True) -> SkillResult:
        check = self.check(target, ref_frame)
        if not check.success:
            return check
        return self.execute(target, ref_frame, blocking) #type: ignore
