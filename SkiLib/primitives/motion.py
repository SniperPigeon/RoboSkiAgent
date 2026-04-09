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
            elif isinstance(target, Item):
                original_frame = self.robot.getLink(robolink.ITEM_TYPE_FRAME)
                if not original_frame.Valid():
                    original_frame = self.robot.Parent()
                try:
                    self.robot.setPoseFrame(target.Parent())
                    self.robot.MoveJ(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(original_frame)
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
            # TODO: error_type is over-broadly mapped to ERROR_TIMEOUT here.
            # RoboDK communication failures (socket disconnect, process crash) and
            # execution anomalies (unexpected joint limit, servo fault) are currently
            # indistinguishable. A finer-grained mapping is needed so that Executor
            # Layer-1 retry logic can correctly identify retriable vs non-retriable failures.
            # Suggested split: ERROR_COMMS for robolink socket errors, ERROR_TIMEOUT for
            # genuine timeouts, and preserve ERROR_TIMEOUT only as a catch-all fallback.
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                message=f"MoveJ failed during execution: {type(e).__name__}",
                robot_state=_snapshot(self.robot),
                suggestion="Check the robot connection and station state.",
            )

    def try_execute(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, blocking, ref_frame) #type: ignore


class MoveL(BasePrimitive):
    def __init__(self, robot_object, RDK_object):
        self.robot: robolink.Item     = robot_object
        self.RDK:   robolink.Robolink = RDK_object
        
    def check(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None) -> SkillResult:
        # Validate target type and ref_frame requirement
        if isinstance(target, robomath.Mat):
            if ref_frame is None:
                return SkillResult(
                    success=False,
                    execution_phase=ExecutionPhase.VALIDATION,
                    error_type=ERROR_MISSING_REF_FRAME,
                    message="A pose target must be accompanied by a reference frame.",
                    suggestion="Provide a reference frame when using pose targets.",
                )
        elif not isinstance(target, (Item, list)):
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.VALIDATION,
                error_type=ERROR_INVALID_PARAM,
                message="Invalid target type. Target must be an Item, a list of joint values, or a Mat pose.",
                suggestion="Provide a valid target: Item, list of joint values, or Mat pose with reference frame.",
            )

        start = self.robot.Joints()

        # Resolve target to a Mat pose for MoveL_Test (which only accepts Mat as pose arg).
        # MoveL_Test return codes (never raises):
        #   0  → path valid and collision-free
        #  -2  → target pose unreachable (IK failure at endpoint)
        #  -1  → target reachable but linear path infeasible (singularity or workspace boundary
        #         violated along the Cartesian trajectory)
        #  > 0 → number of collision pairs detected
        if isinstance(target, robomath.Mat):
            assert ref_frame is not None  # guaranteed by the validation block above
            # Express pose in robot base frame so MoveL_Test uses a consistent reference.
            target_pose: robomath.Mat = ref_frame * target
        elif isinstance(target, list):
            # Joint values → FK gives pose in robot base frame.
            target_pose = self.robot.SolveFK(target)
        else:
            # Item: set active frame to target's parent so MoveL_Test uses the correct reference.
            original_frame = self.robot.getLink(robolink.ITEM_TYPE_FRAME)
            self.robot.setPoseFrame(target.Parent())
            target_pose = target.Pose()

        self.RDK.setCollisionActive(True)
        test_result = self.robot.MoveL_Test(start, target_pose)
        self.RDK.setCollisionActive(False)

        if isinstance(target, Item):
            self.robot.setPoseFrame(original_frame)  # restore after MoveL_Test

        if test_result == 0:
            return SkillResult(
                success=True,
                execution_phase=ExecutionPhase.PLANNING,
                message="Linear path is valid and collision-free.",
            )
        if test_result == -2:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_IK_FAILURE,
                message="Target pose is outside the robot's reachable workspace.",
                suggestion=(
                    "Verify the target coordinates and orientation. "
                    "Consider adjusting the approach direction or using a different configuration."
                ),
            )
        if test_result == -1:
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.PLANNING,
                error_type=ERROR_IK_FAILURE,
                message="Target is reachable but the linear path passes through a singularity or workspace boundary.",
                suggestion=(
                    "The robot cannot maintain a straight-line Cartesian path to the target. "
                    "Use MoveJ to avoid the singularity, or approach from a different direction."
                ),
            )
        return SkillResult(
            success=False,
            execution_phase=ExecutionPhase.PLANNING,
            error_type=ERROR_COLLISION,
            message="Linear path would cause collisions in the station.",
            data={"collision_count": test_result},
            suggestion=(
                "This count includes all collisions in the station, not just those on the linear path. "
                "Some may be external or implicitly caused by this move. "
                "Check the collision map to identify all collision pairs and adjust the approach direction."
            ),
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
            elif isinstance(target, Item):
                original_frame = self.robot.getLink(robolink.ITEM_TYPE_FRAME)
                if not original_frame.Valid():
                    original_frame = self.robot.Parent()  # 回退到 base frame
                try:
                    self.robot.setPoseFrame(target.Parent())
                    self.robot.MoveL(target, blocking=blocking)
                finally:
                    self.robot.setPoseFrame(original_frame)  # 保证永远能恢复原始参考系
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
            # TODO: same over-broad error_type mapping as MoveJ — see MoveJ.execute()
            return SkillResult(
                success=False,
                execution_phase=ExecutionPhase.EXECUTION,
                error_type=ERROR_TIMEOUT,
                message=f"MoveL failed during execution: {type(e).__name__}",
                robot_state=_snapshot(self.robot),
                suggestion="Check the robot connection and station state.",
            )

    def try_execute(self, target: Union[Item, List[float], robomath.Mat], ref_frame: Optional[robomath.Mat] = None, blocking: bool = True) -> SkillResult:
        if not self._should_skip_check():
            check = self.check(target, ref_frame)
            if not check.success:
                return check
        return self.execute(target, ref_frame, blocking) #type: ignore
