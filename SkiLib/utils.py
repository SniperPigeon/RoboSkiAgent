"""
SkiLib Utilities
Utility Functions Module

Provides commonly used utility functions for robot skill development, including:
- IK Solver: Safe inverse kinematics solving
- Joint distance calculation
- Other auxiliary functions
"""

import math
from typing import List, Optional, Tuple, Union
from robodk import robolink, robomath
from robodk.robolink import Item
import numpy as np

from .base import CheckResult


class IKSolver:
    """
    Safe Inverse Kinematics Solver
    
    Features:
    - Get all IK solutions
    - Select the solution closest to current configuration
    - Validate joint limits
    - Detect singularity neighborhoods
    - Provide detailed diagnostic information
    
    Example:
        >>> solver = IKSolver(robot, joint_weights=[2.0, 1.5, 1.0, 0.5, 0.5, 0.3])
        >>> result, joints = solver.solve(target_pose)
        >>> if result.is_valid:
        >>>     print(f"Solution found: {joints}")
    """
    
    def __init__(
        self, 
        robot: Item,
        joint_weights: Optional[List[float]] = None,
        singularity_threshold: float = 0.01,
        check_limits: bool = True,
        check_singularities: bool = True
    ):
        """
        Initialize IK Solver
        
        Args:
            robot: RoboDK robot object
            joint_weights: Joint distance calculation weights (default: equal weights)
            singularity_threshold: Singularity detection threshold (manipulability, smaller means closer to singularity)
            check_limits: Whether to check joint limits
            check_singularities: Whether to check singularities
        """
        self.robot = robot
        self.n_joints = len(robot.Joints().list())
        
        # Set joint weights (default: equal)
        if joint_weights is None:
            self.joint_weights = [1.0] * self.n_joints
        else:
            if len(joint_weights) != self.n_joints:
                raise ValueError(f"Number of joint weights ({len(joint_weights)}) must equal number of joints ({self.n_joints})")
            self.joint_weights = joint_weights
        
        self.singularity_threshold = singularity_threshold
        self.check_limits = check_limits
        self.check_singularities = check_singularities
        
        # Get joint limits
        self._joint_limits = self._get_joint_limits()
    
    def _get_joint_limits(self) -> List[Tuple[float, float]]:
        """Get robot joint limits"""
        limits = []
        joints_info = self.robot.JointLimits()
        
        # RoboDK returns [lower_limits], [upper_limits]
        lower_limits = joints_info[0]
        upper_limits = joints_info[1]
        
        for i in range(self.n_joints):
            limits.append((lower_limits[i], upper_limits[i]))
        
        return limits
    
    def solve(
        self, 
        target_pose: robomath.Mat,
        current_joints: Optional[List[float]] = None,
        reference_frame: Optional[Item] = None,
        tool: Optional[Item] = None
    ) -> Tuple[CheckResult, Optional[List[float]]]:
        """
        Safe IK solving
        
        Args:
            target_pose: Target pose matrix
            current_joints: Current joint configuration (None uses robot's current position)
            reference_frame: Reference coordinate frame
            tool: Tool coordinate frame
            
        Returns:
            (CheckResult, joints): Check result and optimal joint solution
        """
        # Get current joint configuration
        if current_joints is None:
            current_joints = self.robot.Joints().list()
        
        # 1. Get all IK solutions
        all_solutions = self._get_all_ik_solutions(target_pose, reference_frame, tool)
        
        if not all_solutions:
            return CheckResult(
                is_valid=False,
                message="Target pose has no inverse kinematics solution (may be outside workspace or pose unreachable)"
            ), None
        
        # 2. Calculate scores for all solutions
        scored_solutions = []
        for solution in all_solutions:
            score, diagnostics = self._evaluate_solution(solution, current_joints)
            scored_solutions.append({
                'joints': solution,
                'score': score,
                'diagnostics': diagnostics
            })
        
        # 3. Sort by score (lower is better)
        scored_solutions.sort(key=lambda x: x['score'])
        
        # 4. Select best solution
        best = scored_solutions[0]
        
        # 5. Validate best solution
        is_valid, message = self._validate_solution(best)
        
        if is_valid:
            return CheckResult(
                is_valid=True,
                message=f"Found valid IK solution ({len(all_solutions)} total solutions, distance={best['score']:.4f})"
            ), best['joints']
        else:
            # Try suboptimal solutions
            for i, candidate in enumerate(scored_solutions[1:], 1):
                is_valid, msg = self._validate_solution(candidate)
                if is_valid:
                    return CheckResult(
                        is_valid=True,
                        message=f"Using {i+1}th best solution (best solution {message}, distance={candidate['score']:.4f})"
                    ), candidate['joints']
            
            # All solutions are invalid
            return CheckResult(
                is_valid=False,
                message=f"Found {len(all_solutions)} IK solutions, but all invalid. Best solution issue: {message}"
            ), None
    
    def _get_all_ik_solutions(
        self, 
        target_pose: robomath.Mat,
        reference_frame: Optional[Item] = None,
        tool: Optional[Item] = None
    ) -> List[List[float]]:
        """Get all IK solutions"""
        try:
            # Save current reference frame and tool
            old_frame = self.robot.PoseFrame()
            old_tool = self.robot.PoseTool()
            
            # Set reference frame and tool (if provided)
            if reference_frame is not None:
                self.robot.setPoseFrame(reference_frame)
            if tool is not None:
                self.robot.setPoseTool(tool)
            
            # Get all IK solutions
            all_solutions = self.robot.SolveIK_All(target_pose)
            
            # Restore original reference frame and tool
            self.robot.setPoseFrame(old_frame)
            self.robot.setPoseTool(old_tool)
            
            # Convert to list format
            if all_solutions is None or len(all_solutions) == 0:
                return []
            
            # RoboDK returns a list of joint values
            solutions = [sol.list() if hasattr(sol, 'list') else list(sol) 
                        for sol in all_solutions]
            
            return solutions
            
        except Exception as e:
            print(f"IK solving error: {e}")
            return []
    
    def _evaluate_solution(
        self, 
        solution: List[float], 
        current_joints: List[float]
    ) -> Tuple[float, dict]:
        """
        Evaluate the quality of an IK solution
        
        Returns:
            (score, diagnostics): Score (lower is better) and diagnostic information
        """
        diagnostics = {}
        
        # 1. Calculate joint space distance (primary score)
        distance = calculate_joint_distance(current_joints, solution, self.joint_weights)
        score = distance
        diagnostics['joint_distance'] = distance
        
        # 2. Joint limit check (if enabled)
        if self.check_limits:
            limits_ok, limits_info = self._check_joint_limits(solution)
            diagnostics['within_limits'] = limits_ok
            diagnostics['limits_info'] = limits_info
            
            if not limits_ok:
                # Exceeded limits, significantly increase score (penalty)
                score += 1000.0
        
        # 3. Singularity check (if enabled)
        if self.check_singularities:
            singularity_measure = self._check_singularity(solution)
            diagnostics['manipulability'] = singularity_measure
            
            if singularity_measure < self.singularity_threshold:
                # Close to singularity, increase score (penalty)
                score += 100.0 * (self.singularity_threshold - singularity_measure)
        
        return score, diagnostics
    
    def _validate_solution(self, solution_data: dict) -> Tuple[bool, str]:
        """Validate if solution is usable"""
        diagnostics = solution_data['diagnostics']
        
        # Check joint limits
        if self.check_limits and not diagnostics.get('within_limits', True):
            limits_info = diagnostics.get('limits_info', {})
            violations = limits_info.get('violations', [])
            return False, f"Joint limit exceeded: {violations}"
        
        # Check singularities
        if self.check_singularities:
            manipulability = diagnostics.get('manipulability', 1.0)
            if manipulability < self.singularity_threshold:
                return False, f"Close to singularity (manipulability={manipulability:.4f})"
        
        return True, "OK"
    
    def _check_joint_limits(self, joints: List[float]) -> Tuple[bool, dict]:
        """Check if joints are within limits"""
        within_limits = True
        violations = []
        margins = []
        
        for i, (joint_val, (lower, upper)) in enumerate(zip(joints, self._joint_limits)):
            if joint_val < lower or joint_val > upper:
                within_limits = False
                violations.append({
                    'joint': i,
                    'value': joint_val,
                    'limit': (lower, upper),
                    'exceed': joint_val - upper if joint_val > upper else lower - joint_val
                })
            
            # Calculate safety margin (distance to nearest limit)
            margin = min(joint_val - lower, upper - joint_val)
            margins.append(margin)
        
        info = {
            'violations': violations,
            'margins': margins,
            'min_margin': min(margins) if margins else 0
        }
        
        return within_limits, info
    
    def _check_singularity(self, joints: List[float]) -> float:
        """
        Check if configuration is close to singularity
        
        Uses manipulability measure as indicator
        Smaller manipulability means closer to singularity
        
        Returns:
            manipulability: Manipulability measure (0 means singularity)
        """
        try:
            # Save current joints
            old_joints = self.robot.Joints()
            
            # Move to configuration to check
            self.robot.setJoints(joints)
            
            # Calculate manipulability (numerical method)
            manipulability = self._calculate_manipulability_numeric(joints)
            
            # Restore joints
            self.robot.setJoints(old_joints)
            
            return manipulability
            
        except Exception as e:
            print(f"Singularity check error: {e}")
            return 1.0  # Assume non-singular
    
    def _calculate_manipulability_numeric(self, joints: List[float]) -> float:
        """
        Calculate manipulability using numerical method
        
        Uses the reciprocal of condition number as manipulability estimate
        """
        try:
            # Get current pose
            pose = self.robot.SolveFK(joints)
            
            # Numerical Jacobian: Small perturbation to each joint, observe end-effector pose change
            epsilon = 1e-4  # Small perturbation
            jacobian = []
            
            for i in range(self.n_joints):
                # Forward perturbation
                joints_plus = joints.copy()
                joints_plus[i] += epsilon
                pose_plus = self.robot.SolveFK(joints_plus)
                
                # Calculate pose difference (position part)
                delta_pose = pose_plus - pose
                delta_xyz = delta_pose.Pos()  # Position change
                
                # Jacobian column (position part)
                jacobian.append([delta_xyz[0]/epsilon, 
                               delta_xyz[1]/epsilon, 
                               delta_xyz[2]/epsilon])
            
            # Convert to numpy array
            J = np.array(jacobian).T  # 3xN matrix (position only)
            
            # Calculate singular values
            singular_values = np.linalg.svd(J, compute_uv=False)
            
            # Manipulability = minimum singular value
            manipulability = np.min(singular_values) if len(singular_values) > 0 else 0.0
            
            return float(manipulability)
            
        except Exception as e:
            print(f"Manipulability calculation error: {e}")
            return 1.0  # Assume normal


def calculate_joint_distance(
    joints1: List[float], 
    joints2: List[float],
    weights: Optional[List[float]] = None
) -> float:
    """
    Calculate weighted Euclidean distance between two joint configurations
    
    Args:
        joints1: Joint configuration 1
        joints2: Joint configuration 2
        weights: Weights (None means equal weights)
        
    Returns:
        Weighted distance
        
    Example:
        >>> distance = calculate_joint_distance(
        ...     [0, 0, 0, 0, 0, 0],
        ...     [10, 5, 0, 0, 0, 0],
        ...     weights=[2.0, 1.0, 1.0, 0.5, 0.5, 0.3]
        ... )
    """
    if len(joints1) != len(joints2):
        raise ValueError(f"Joint configuration dimensions don't match: {len(joints1)} vs {len(joints2)}")
    
    if weights is None:
        weights = [1.0] * len(joints1)
    
    if len(weights) != len(joints1):
        raise ValueError(f"Number of weights ({len(weights)}) must equal number of joints ({len(joints1)})")
    
    # Weighted Euclidean distance
    distance_squared = sum(
        w * (j1 - j2) ** 2 
        for w, j1, j2 in zip(weights, joints1, joints2)
    )
    
    return math.sqrt(distance_squared)


def find_closest_ik_solution(
    robot: Item,
    target_pose: robomath.Mat,
    current_joints: Optional[List[float]] = None,
    reference_frame: Optional[Item] = None,
    tool: Optional[Item] = None,
    joint_weights: Optional[List[float]] = None,
    check_limits: bool = True,
    check_singularities: bool = True,
    singularity_threshold: float = 0.01
) -> Tuple[CheckResult, Optional[List[float]]]:
    """
    Find the closest IK solution to current configuration (convenience function)
    
    Args:
        robot: RoboDK robot object
        target_pose: Target pose
        current_joints: Current joint configuration (None uses robot's current position)
        reference_frame: Reference coordinate frame
        tool: Tool coordinate frame
        joint_weights: Joint weights
        check_limits: Whether to check joint limits
        check_singularities: Whether to check singularities
        singularity_threshold: Singularity threshold
        
    Returns:
        (CheckResult, joints): Check result and optimal joint solution
        
    Example:
        >>> result, joints = find_closest_ik_solution(robot, target_pose)
        >>> if result.is_valid:
        >>>     robot.MoveJ(joints)
    """
    solver = IKSolver(
        robot=robot,
        joint_weights=joint_weights,
        singularity_threshold=singularity_threshold,
        check_limits=check_limits,
        check_singularities=check_singularities
    )
    
    return solver.solve(
        target_pose=target_pose,
        current_joints=current_joints,
        reference_frame=reference_frame,
        tool=tool
    )
