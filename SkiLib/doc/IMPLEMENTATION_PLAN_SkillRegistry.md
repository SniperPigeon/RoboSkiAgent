# Skill Registry 实现计划

## 概述
实现一个自动技能注册系统，包含主动加载、单例模式和 LLM 工具模式生成。

## 设计决策
- **初始化模式**: 主动加载（启动时加载所有技能）
- **机器人上下文**: 单例模式，外部实例传入注册器
- **技能实例**: 每种技能类型单例
- **LLM 模式**: 启动时生成并缓存

---

## 1. 增强的数据结构

### 1.1 SkillMetadata（技能元数据）
```python
# SkiLib/base.py

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum

class SkillCategory(Enum):
    PRIMITIVE = "primitive"
    SKILL = "skill"
    TASK = "task"

@dataclass
class SkillParameter:
    """技能参数定义"""
    name: str
    type: str  # "Item", "List[float]", "Mat", "bool", etc.
    description: str
    required: bool = True
    default: Any = None
    
    def to_json_schema(self) -> Dict:
        """转换为 JSON Schema 格式"""
        schema = {
            "type": self._python_type_to_json_type(self.type),
            "description": self.description
        }
        if self.default is not None:
            schema["default"] = self.default
        return schema
    
    @staticmethod
    def _python_type_to_json_type(py_type: str) -> str:
        type_mapping = {
            "bool": "boolean",
            "int": "integer",
            "float": "number",
            "str": "string",
            "List[float]": "array",
            "dict": "object"
        }
        return type_mapping.get(py_type, "string")


@dataclass
class SkillMetadata:
    """用于技能注册和 LLM 工具生成的元数据"""
    name: str
    description: str
    category: SkillCategory
    parameters: List[SkillParameter] = field(default_factory=list)
    returns: str = "Any"
    preconditions: List[str] = field(default_factory=list)
    effects: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    version: str = "1.0"
    
    def to_llm_tool_schema(self, format: str = "openai") -> Dict:
        """
        生成 LLM 工具调用模式
        
        Args:
            format: "openai" 或 "anthropic"
        
        Returns:
            工具模式字典
        """
        if format == "openai":
            return self._to_openai_schema()
        elif format == "anthropic":
            return self._to_anthropic_schema()
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def _to_openai_schema(self) -> Dict:
        """OpenAI 函数调用格式"""
        properties = {}
        required = []
        
        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)
        
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
    
    def _to_anthropic_schema(self) -> Dict:
        """Anthropic 工具使用格式"""
        properties = {}
        required = []
        
        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)
        
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
```

---

## 2. SkillRegistry 实现

### 2.1 核心注册器类
```python
# SkiLib/registry.py

from typing import Dict, List, Type, Optional, Any
from SkiLib.base import BaseSkill, SkillMetadata, SkillCategory
from SkiLib.robotcontext import RobotContext
import logging

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    用于自动技能发现和管理的单例注册器。
    
    生命周期:
    1. 模块导入: 装饰器注册技能类
    2. set_robot_context(): 提供机器人实例
    3. initialize_all_skills(): 主动加载所有技能（自动）
    4. get_skill(): 访问技能实例
    5. get_llm_tool_schemas(): 生成 LLM 工具
    """
    
    _instance: Optional['SkillRegistry'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        # 只初始化一次
        if self._initialized:
            return
        
        self._skill_classes: Dict[str, Type[BaseSkill]] = {}
        self._skill_instances: Dict[str, BaseSkill] = {}
        self._metadata: Dict[str, SkillMetadata] = {}
        self._robot_context: Optional[RobotContext] = None
        self._llm_schemas_cache: Dict[str, List[Dict]] = {}
        self._initialized = True
        
        logger.info("SkillRegistry initialized")
    
    def register(self, skill_class: Type[BaseSkill], metadata: SkillMetadata):
        """
        注册带有元数据的技能类。
        在模块导入时由 @skill 装饰器调用。
        
        Args:
            skill_class: 技能类（非实例）
            metadata: 技能元数据
        """
        name = metadata.name
        
        if name in self._skill_classes:
            logger.warning(f"Skill '{name}' already registered, overwriting")
        
        self._skill_classes[name] = skill_class
        self._metadata[name] = metadata
        
        logger.debug(f"Registered skill: {name} (category: {metadata.category.value})")
    
    def set_robot_context(self, robot_context: RobotContext):
        """
        设置机器人上下文并自动初始化所有技能（主动加载）。
        
        Args:
            robot_context: RobotContext 实例
        """
        if self._robot_context is not None:
            logger.warning("Robot context already set, replacing")
        
        self._robot_context = robot_context
        logger.info(f"Robot context set: {robot_context.robot_name}")
        
        # 主动初始化
        self.initialize_all_skills()
        
        # 生成 LLM 模式
        self._generate_llm_schemas()
    
    def initialize_all_skills(self):
        """
        主动初始化：一次性创建所有技能实例。
        由 set_robot_context() 自动调用。
        """
        if self._robot_context is None:
            raise RuntimeError("Cannot initialize skills without robot context")
        
        logger.info(f"Initializing {len(self._skill_classes)} skills...")
        
        for name, skill_class in self._skill_classes.items():
            try:
                instance = skill_class(
                    robot_object=self._robot_context.robot,
                    RDK_object=self._robot_context.RDK
                )
                self._skill_instances[name] = instance
                logger.debug(f"Initialized skill: {name}")
            except Exception as e:
                logger.error(f"Failed to initialize skill '{name}': {e}")
                raise
        
        logger.info(f"Successfully initialized {len(self._skill_instances)} skills")
    
    def get_skill(self, name: str) -> BaseSkill:
        """
        根据名称获取技能实例。
        
        Args:
            name: 技能名称
        
        Returns:
            技能实例
        
        Raises:
            KeyError: 如果未找到技能
            RuntimeError: 如果技能未初始化
        """
        if name not in self._skill_instances:
            if name in self._skill_classes:
                raise RuntimeError(
                    f"Skill '{name}' registered but not initialized. "
                    "Call set_robot_context() first."
                )
            else:
                raise KeyError(f"Skill '{name}' not found. Available: {self.list_skills()}")
        
        return self._skill_instances[name]
    
    def list_skills(self, category: Optional[SkillCategory] = None) -> List[str]:
        """
        列出所有已注册的技能，可选按类别过滤。
        
        Args:
            category: 按类别过滤（None 表示全部）
        
        Returns:
            技能名称列表
        """
        if category is None:
            return list(self._metadata.keys())
        else:
            return [
                name for name, meta in self._metadata.items()
                if meta.category == category
            ]
    
    def get_metadata(self, name: str) -> SkillMetadata:
        """获取技能元数据"""
        if name not in self._metadata:
            raise KeyError(f"Skill '{name}' not found")
        return self._metadata[name]
    
    def _generate_llm_schemas(self):
        """在启动时生成并缓存 LLM 工具模式"""
        logger.info("Generating LLM tool schemas...")
        
        for format in ["openai", "anthropic"]:
            self._llm_schemas_cache[format] = [
                meta.to_llm_tool_schema(format)
                for meta in self._metadata.values()
            ]
        
        logger.info(f"Generated schemas for {len(self._metadata)} skills")
    
    def get_llm_tool_schemas(
        self,
        format: str = "openai",
        category: Optional[SkillCategory] = None
    ) -> List[Dict]:
        """
        获取 LLM 工具模式（缓存）。
        
        Args:
            format: "openai" 或 "anthropic"
            category: 按类别过滤（None 表示全部）
        
        Returns:
            工具模式列表
        """
        if format not in self._llm_schemas_cache:
            raise ValueError(f"Unsupported format: {format}")
        
        schemas = self._llm_schemas_cache[format]
        
        # 如果指定了类别，进行过滤
        if category is not None:
            category_names = self.list_skills(category)
            schemas = [
                s for s in schemas
                if s.get("function", {}).get("name") in category_names  # OpenAI
                or s.get("name") in category_names  # Anthropic
            ]
        
        return schemas
    
    def __getitem__(self, name: str) -> BaseSkill:
        """允许字典式访问: registry['moveJ']"""
        return self.get_skill(name)
    
    def __contains__(self, name: str) -> bool:
        """支持 'in' 操作符"""
        return name in self._skill_instances


# 全局单例实例
registry = SkillRegistry()
```

---

## 3. 装饰器实现

### 3.1 @skill 装饰器
```python
# SkiLib/decorators.py

from typing import List, Type, Optional
from SkiLib.base import BaseSkill, SkillMetadata, SkillCategory, SkillParameter
from SkiLib.registry import registry


def skill(
    name: Optional[str] = None,
    description: str = "",
    category: str = "skill",
    parameters: Optional[List[dict]] = None,
    returns: str = "Any",
    preconditions: Optional[List[str]] = None,
    effects: Optional[List[str]] = None,
    examples: Optional[List[str]] = None,
    version: str = "1.0"
):
    """
    自动注册技能的装饰器。
    
    用法:
        @skill(
            name="moveJ",
            description="移动机器人关节到目标位置",
            category="primitive",
            parameters=[
                {"name": "target", "type": "Item|List[float]", "description": "目标位置", "required": True},
                {"name": "blocking", "type": "bool", "description": "等待完成", "default": True}
            ]
        )
        class moveJ(BaseSkill):
            ...
    
    Args:
        name: 技能名称（默认为类名）
        description: 人类可读描述
        category: "primitive", "skill" 或 "task"
        parameters: 参数定义列表
        returns: 返回类型描述
        preconditions: 前置条件描述列表
        effects: 效果描述列表
        examples: 使用示例
        version: 技能版本
    """
    def decorator(cls: Type[BaseSkill]) -> Type[BaseSkill]:
        # 如果未提供名称，使用类名
        skill_name = name or cls.__name__
        
        # 将类别字符串转换为枚举
        try:
            category_enum = SkillCategory(category.lower())
        except ValueError:
            raise ValueError(f"Invalid category: {category}. Must be primitive, skill, or task")
        
        # 将参数字典转换为 SkillParameter 对象
        param_objects = []
        if parameters:
            for param_dict in parameters:
                param_objects.append(SkillParameter(
                    name=param_dict["name"],
                    type=param_dict["type"],
                    description=param_dict["description"],
                    required=param_dict.get("required", True),
                    default=param_dict.get("default", None)
                ))
        
        # 创建元数据
        metadata = SkillMetadata(
            name=skill_name,
            description=description,
            category=category_enum,
            parameters=param_objects,
            returns=returns,
            preconditions=preconditions or [],
            effects=effects or [],
            examples=examples or [],
            version=version
        )
        
        # 注册到全局注册器
        registry.register(cls, metadata)
        
        # 将元数据附加到类以便内省
        cls._skill_metadata = metadata
        
        return cls
    
    return decorator
```

---

## 4. 更新现有文件

### 4.1 增强的 CheckResult
```python
# SkiLib/base.py（添加到现有文件）

from enum import Enum
from typing import List, Dict, Any, Optional

class CheckSeverity(Enum):
    """检查结果的严重程度级别"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

@dataclass
class CheckIssue:
    """单个检查问题"""
    severity: CheckSeverity
    message: str
    check_name: str
    suggestion: Optional[str] = None

@dataclass
class CheckResult:
    """带有详细反馈的增强检查结果"""
    is_valid: bool
    message: str = ""
    issues: List[CheckIssue] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def add_error(self, message: str, check_name: str, suggestion: str = None):
        """添加错误问题"""
        self.is_valid = False
        self.issues.append(CheckIssue(
            severity=CheckSeverity.ERROR,
            message=message,
            check_name=check_name,
            suggestion=suggestion
        ))
    
    def add_warning(self, message: str, check_name: str, suggestion: str = None):
        """添加警告（不会使检查无效）"""
        self.issues.append(CheckIssue(
            severity=CheckSeverity.WARNING,
            message=message,
            check_name=check_name,
            suggestion=suggestion
        ))
    
    def add_info(self, message: str, check_name: str):
        """添加信息性消息"""
        self.issues.append(CheckIssue(
            severity=CheckSeverity.INFO,
            message=message,
            check_name=check_name
        ))
    
    def get_llm_feedback(self) -> str:
        """格式化反馈以供 LLM 自我修正"""
        if self.is_valid:
            return f"✓ {self.message}"
        
        feedback_parts = [f"✗ {self.message}"]
        
        for issue in self.issues:
            if issue.severity == CheckSeverity.ERROR:
                feedback_parts.append(f"  ERROR [{issue.check_name}]: {issue.message}")
                if issue.suggestion:
                    feedback_parts.append(f"    → Suggestion: {issue.suggestion}")
        
        return "\n".join(feedback_parts)
```

### 4.2 RobotContext 单例
```python
# SkiLib/robotcontext.py（更新）

from robodk import robolink
from robodk import robomath
from typing import Optional

class RobotContext:
    """
    RoboDK 机器人连接的单例上下文管理器。
    
    用法:
        context = RobotContext()  # 首次调用创建实例
        context2 = RobotContext()  # 返回相同实例
    """
    
    _instance: Optional['RobotContext'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        # 只初始化一次
        if self._initialized:
            return
        
        self.RDK = robolink.Robolink()
        robot = self.RDK.Item('', robolink.ITEM_TYPE_ROBOT)
        
        if robot is None or not robot.Valid():
            raise Exception("No robot found in the RoboDK station")
        
        self.robot = robot
        self.robot_name = robot.Name()
        self._initialized = True
    
    def get_all_items(self, item_type: Optional[int] = None) -> List[str]:
        """
        获取 RoboDK 树中的所有项目。
        
        Args:
            item_type: 按类型过滤（None 表示全部）
        
        Returns:
            项目名称列表
        """
        items = self.RDK.ItemList(item_type)
        return [item.Name() for item in items if item.Valid()]
    
    def get_reference_frames(self) -> List[str]:
        """获取工作站中的所有参考坐标系"""
        return self.get_all_items(robolink.ITEM_TYPE_FRAME)
    
    def get_tools(self) -> List[str]:
        """获取工作站中的所有工具"""
        return self.get_all_items(robolink.ITEM_TYPE_TOOL)
    
    def get_targets(self) -> List[str]:
        """获取工作站中的所有目标点"""
        return self.get_all_items(robolink.ITEM_TYPE_TARGET)
```

### 4.3 用装饰器更新 moveJ
```python
# SkiLib/primitives/motion.py（更新）

from SkiLib.base import BaseSkill, CheckResult
from SkiLib.decorators import skill
from robodk import robolink, robomath
from robodk.robolink import Item
from typing import Union, List

@skill(
    name="moveJ",
    description="使用关节插值移动机器人关节到目标位置",
    category="primitive",
    parameters=[
        {
            "name": "target",
            "type": "Item|List[float]|Mat",
            "description": "作为 Item、关节值或姿态矩阵的目标位置",
            "required": True
        },
        {
            "name": "start",
            "type": "List[float]",
            "description": "起始关节配置（None 表示当前位置）",
            "required": False,
            "default": None
        },
        {
            "name": "ref_frame",
            "type": "Mat",
            "description": "姿态目标的参考坐标系",
            "required": False,
            "default": None
        },
        {
            "name": "blocking",
            "type": "bool",
            "description": "等待移动完成",
            "required": False,
            "default": True
        }
    ],
    returns="List[float]|Mat",
    preconditions=[
        "机器人必须处于有效状态",
        "目标必须可达",
        "路径必须无碰撞"
    ],
    effects=[
        "机器人移动到目标位置",
        "关节值更新"
    ],
    examples=[
        "moveJ(target=robot_target)",
        "moveJ(target=[0, 45, 90, 0, -45, 0], blocking=True)",
        "moveJ(target=pose_matrix, ref_frame=world_frame)"
    ]
)
class moveJ(BaseSkill):
    # ... 现有实现 ...
    pass
```

---

## 5. 自动发现机制

### 5.1 包 __init__.py
```python
# SkiLib/__init__.py

from SkiLib.registry import registry, SkillRegistry
from SkiLib.robotcontext import RobotContext
from SkiLib.base import BaseSkill, CheckResult, SkillCategory
from SkiLib.decorators import skill

# 自动导入所有 primitives 和 skills 以触发装饰器
import importlib
import pkgutil
from pathlib import Path

def _auto_import_skills():
    """自动导入所有技能模块以触发 @skill 装饰器"""
    package_dir = Path(__file__).parent
    
    # 导入 primitives/ 中的所有模块
    primitives_dir = package_dir / "primitives"
    if primitives_dir.exists():
        for module_info in pkgutil.iter_modules([str(primitives_dir)]):
            try:
                importlib.import_module(f"SkiLib.primitives.{module_info.name}")
            except Exception as e:
                print(f"Warning: Failed to import primitive '{module_info.name}': {e}")
    
    # 导入 skills/ 中的所有模块
    skills_dir = package_dir / "skills"
    if skills_dir.exists():
        for module_info in pkgutil.iter_modules([str(skills_dir)]):
            try:
                importlib.import_module(f"SkiLib.skills.{module_info.name}")
            except Exception as e:
                print(f"Warning: Failed to import skill '{module_info.name}': {e}")

# 包被导入时触发自动导入
_auto_import_skills()

__all__ = [
    'registry',
    'SkillRegistry',
    'RobotContext',
    'BaseSkill',
    'CheckResult',
    'SkillCategory',
    'skill'
]
```

---

## 6. 更新后的 main.py

```python
# SkiLib/main.py

import os
import sys
import logging

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 处理导入路径
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

# 导入注册器和上下文（自动发现在此发生）
from SkiLib import registry, RobotContext

def initialize_skill_system():
    """使用机器人上下文初始化技能系统"""
    logger.info("Initializing skill system...")
    
    # 创建机器人上下文（单例）
    robot_context = RobotContext()
    logger.info(f"Connected to robot: {robot_context.robot_name}")
    
    # 设置上下文并主动加载所有技能
    registry.set_robot_context(robot_context)
    
    # 记录已注册的技能
    primitives = registry.list_skills(category="primitive")
    skills = registry.list_skills(category="skill")
    logger.info(f"Loaded {len(primitives)} primitives: {primitives}")
    logger.info(f"Loaded {len(skills)} skills: {skills}")
    
    return robot_context

def print_llm_tools(format="openai", category=None):
    """打印 LLM 工具模式"""
    schemas = registry.get_llm_tool_schemas(format=format, category=category)
    
    print(f"\n=== LLM Tool Schemas ({format}) ===")
    import json
    print(json.dumps(schemas, indent=2))

if __name__ == "__main__":
    # 初始化
    robot_context = initialize_skill_system()
    RDK = robot_context.RDK
    
    # 示例 1: 从注册器使用技能
    target = RDK.Item("App Pick Part A")
    move_skill = registry['moveJ']
    
    check_result = move_skill.check(target=target)
    print(f"\nCheck result: {check_result.message}")
    
    if check_result.is_valid:
        print("Executing movement...")
        final_pos = move_skill.execute(target)
        print(f"Movement complete: {final_pos}")
    else:
        print(f"Movement not safe: {check_result.get_llm_feedback()}")
    
    # 示例 2: 打印 LLM 工具
    print_llm_tools(format="openai", category="primitive")
```

---

## 7. 测试策略

### 7.1 测试脚本
```python
# tests/test_registry.py

import unittest
from SkiLib import registry, RobotContext, SkillCategory

class TestSkillRegistry(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """为所有测试一次性设置机器人上下文"""
        cls.robot_context = RobotContext()
        registry.set_robot_context(cls.robot_context)
    
    def test_singleton_pattern(self):
        """验证注册器是单例"""
        from SkiLib.registry import SkillRegistry
        reg1 = SkillRegistry()
        reg2 = SkillRegistry()
        self.assertIs(reg1, reg2)
    
    def test_robot_context_singleton(self):
        """验证机器人上下文是单例"""
        ctx1 = RobotContext()
        ctx2 = RobotContext()
        self.assertIs(ctx1, ctx2)
    
    def test_skills_loaded(self):
        """验证技能已加载"""
        primitives = registry.list_skills(category=SkillCategory.PRIMITIVE)
        self.assertGreater(len(primitives), 0)
        self.assertIn("moveJ", primitives)
    
    def test_skill_access(self):
        """测试技能检索"""
        skill = registry.get_skill("moveJ")
        self.assertIsNotNone(skill)
        
        # 字典式访问
        skill2 = registry["moveJ"]
        self.assertIs(skill, skill2)  # 相同实例
    
    def test_llm_schema_generation(self):
        """测试 LLM 模式生成"""
        schemas_openai = registry.get_llm_tool_schemas(format="openai")
        schemas_anthropic = registry.get_llm_tool_schemas(format="anthropic")
        
        self.assertGreater(len(schemas_openai), 0)
        self.assertGreater(len(schemas_anthropic), 0)
        
        # 验证模式结构
        schema = schemas_openai[0]
        self.assertIn("function", schema)
        self.assertIn("name", schema["function"])
        self.assertIn("parameters", schema["function"])
    
    def test_metadata_access(self):
        """测试元数据检索"""
        meta = registry.get_metadata("moveJ")
        self.assertEqual(meta.name, "moveJ")
        self.assertEqual(meta.category, SkillCategory.PRIMITIVE)
        self.assertGreater(len(meta.parameters), 0)

if __name__ == '__main__':
    unittest.main()
```

---

## 8. 迁移步骤

1. **创建新文件**:
   - `SkiLib/registry.py`
   - `SkiLib/decorators.py`
   - `tests/test_registry.py`

2. **更新现有文件**:
   - `SkiLib/base.py` - 添加增强的 CheckResult 和 SkillMetadata
   - `SkiLib/robotcontext.py` - 添加单例模式
   - `SkiLib/__init__.py` - 添加自动发现
   - `SkiLib/primitives/motion.py` - 添加 @skill 装饰器

3. **更新 main.py** - 使用新的注册器系统

4. **测试**:
   - 运行单元测试
   - 验证所有技能加载
   - 测试 LLM 模式生成
   - 验证单例模式工作

---

## 9. 未来增强

- **懒加载选项**（如果技能数量增长）
- **动态重载**（开发时热重载技能）
- **技能版本管理**（同一技能的多个版本）
- **依赖注入**（技能依赖其他技能）
- **技能市场**（从外部包加载技能）
- **性能指标**（跟踪 check/execute 时间）
- **LLM 使用跟踪**（哪些技能被调用最多）
