# IK Solver 使用指南

## 快速开始

```python
from SkiLib.utils import find_closest_ik_solution
from robodk import robolink

# 连接机器人
RDK = robolink.Robolink()
robot = RDK.Item('', robolink.ITEM_TYPE_ROBOT)

# 获取目标姿态
target = RDK.Item("Target 1")
target_pose = target.Pose()

# 求解IK
result, joints = find_closest_ik_solution(robot, target_pose)

if result.is_valid:
    print(f"找到解: {joints}")
    robot.MoveJ(joints)
else:
    print(f"失败: {result.message}")
```

## 核心功能

### 1. IKSolver 类

用于需要多次求解的场景：

```python
from SkiLib.utils import IKSolver

# 创建求解器（可复用）
solver = IKSolver(
    robot=robot,
    joint_weights=[2.0, 1.5, 1.0, 0.5, 0.5, 0.3],  # 基座权重高
    check_limits=True,              # 检查关节限位
    check_singularities=True,       # 检查奇异点
    singularity_threshold=0.01      # 奇异点阈值
)

# 求解多个目标
for target in targets:
    result, joints = solver.solve(target.Pose())
    if result.is_valid:
        print(f"解: {joints}")
```

### 2. 关节距离计算

```python
from SkiLib.utils import calculate_joint_distance

distance = calculate_joint_distance(
    joints1=[0, 0, 0, 0, 0, 0],
    joints2=[10, 5, 0, 0, 0, 0],
    weights=[2.0, 1.0, 1.0, 0.5, 0.5, 0.3]  # 可选
)
```

## 参数说明

### 关节权重 (joint_weights)

控制哪些关节更不愿意移动：

```python
# 6轴机器人推荐设置
weights = [
    2.0,   # 关节1 (基座) - 权重高，尽量不动
    1.5,   # 关节2 (肩部)
    1.0,   # 关节3 (肘部)
    0.5,   # 关节4 (腕部1) - 权重低，容易移动
    0.5,   # 关节5 (腕部2)
    0.3    # 关节6 (腕部3)
]
```

### 奇异点阈值 (singularity_threshold)

- `0.01` (默认) - 中等严格度
- `0.05` - 更严格，避开更大范围
- `0.001` - 更宽松，允许更接近奇异点

## 集成到 Primitives

在 `moveJ` 中使用：

```python
from SkiLib.utils import find_closest_ik_solution

class moveJ(BaseSkill):
    def check(self, target, start=None, ref_frame=None):
        if isinstance(target, robomath.Mat):
            # 使用IK求解器
            result, joints = find_closest_ik_solution(
                robot=self.robot,
                target_pose=target,
                current_joints=start or self.robot.Joints().list(),
                reference_frame=ref_frame
            )
            
            if not result.is_valid:
                return CheckResult(False, result.message)
            
            # 继续碰撞检查
            return self._check_collision(joints)
```

## 运行测试

```bash
cd test
python test_ik_solver.py
```

测试覆盖：
- 基本IK求解
- IKSolver类功能
- 关节距离计算
- 实际目标点测试
- 多解选择验证
