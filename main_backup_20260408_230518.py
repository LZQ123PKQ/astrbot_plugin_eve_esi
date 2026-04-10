from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import aiohttp
import json
import os
import time

@register("eve_esi", "PIKApika", "EVE ESI 调用插件", "1.0.0")
class EveESIPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 使用当前目录存储配置
        self.data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(self.data_dir, exist_ok=True)
        # 简称字典文件
        self.alias_file = os.path.join(self.data_dir, "aliases.json")
        # 加载简称字典
        self.aliases = self._load_aliases()
        # 初始化aiohttp ClientSession
        self.session = None
        
        # 初始化加成处理字典（使用英文键，中文翻译在注释中）
        # 注意：如果识别到不同的ESI返回值，应将其加入字典，或检查字典中的键是否与ESI返回值的名称一致
        self.bonus_handlers = {
            # 特殊效果
            'covertOpsAndReconOpsCloakModuleDelayBonus': lambda bv, en: "隐形装置重启延时降到5秒",
            'interceptor2LaserTracking': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台跟踪速度加成",
            'interceptorMWDSignatureRadiusBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低",
            'MWDSignatureRadiusRoleBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低",
            'Interceptor2WarpScrambleRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁扰频器和跃迁扰断器最佳射程加成",
            'shipRocketRoFBonusAF2': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 火箭发射器射速加成",
            'interceptorNullificationRoleBonus': lambda bv, en: None,  # 跳过，会在下面统一处理
            
            # 特有加成
            'covertOpsCloak': lambda bv, en: "可以装备隐秘行动隐形装置和隐秘诱导力场发生器",
            'CovertOps': lambda bv, en: "可以装备隐秘行动隐形装置和隐秘诱导力场发生器",
            'assaultDamageControl': lambda bv, en: "可以装备突击损伤控制装备",
            'Assault': lambda bv, en: "可以装备突击损伤控制装备",
            'HeavyAssault': lambda bv, en: "可以装备突击损伤控制装备",
            'interceptorNullificationRoleBonus': lambda bv, en: "80% 拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低",
            
            # 伤害相关
            'damageMultiplier': lambda bv, en: self._handle_damage_bonus(bv, en, '伤害量调整'),  # 伤害量调整
            'smallWeaponDamageMultiplier': lambda bv, en: self._handle_damage_bonus(bv, en, '小型武器伤害'),  # 小型武器伤害倍增系数
            'mediumWeaponDamageMultiplier': lambda bv, en: self._handle_damage_bonus(bv, en, '中型武器伤害'),  # 中型武器伤害倍增系数
            'emDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 炸弹电磁伤害加成" if 'Bomb' in en else f"{self._format_bonus_value(bv)}% 鱼雷电磁伤害加成" if 'Torpedo' in en else f"{self._format_bonus_value(bv)}% 电磁伤害加成",  # 电磁伤害加成
            'thermalDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 热能伤害加成",  # 热能伤害加成
            'kineticDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 动能伤害加成",  # 动能伤害加成
            'explosiveDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 爆炸伤害加成",  # 爆炸伤害加成
            'emDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 电磁伤害加成",  # 电磁伤害加成
            'explosiveDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸伤害加成",  # 爆炸伤害加成
            'kineticDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 动能伤害加成",  # 动能伤害加成
            'thermalDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 热能伤害加成",  # 热能伤害加成
            
            # 防御相关
            'RemoteArmorRepairAmount': lambda bv, en: f"{self._format_bonus_value(bv)}% 远程装甲维修器维修量加成",  # 远程装甲维修器维修量
            'armorRepairAmount': lambda bv, en: f"{self._format_bonus_value(bv)}% 装甲值维修量加成",  # 装甲值维修量
            'armorRepairMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 装甲维修量加成",  # 修复量倍增系数
            'shieldRepairMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾维修量加成",  # 护盾维修倍增系数
            'shieldTransferMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾传输量加成",  # 护盾传输量倍增系数
            'remoteArmorRepairMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 远距装甲维修量加成",  # 远距维修量倍增系数
            'armorEmDamageResonance': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲电磁伤害抗性",  # 装甲电磁伤害抗性
            'armorThermalDamageResonance': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲热能伤害抗性",  # 装甲热能伤害抗性
            'armorKineticDamageResonance': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲动能伤害抗性",  # 装甲动能伤害抗性
            'armorExplosiveDamageResonance': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲爆炸伤害抗性",  # 装甲爆炸伤害抗性
            'thermalDamageResistanceBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 热能伤害抗性加成",  # 热能伤害抗性加成
            'kineticDamageResistanceBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 动能伤害抗性加成",  # 动能伤害抗性加成
            'explosiveDamageResistanceBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸伤害抗性加成",  # 爆炸伤害抗性加成
            'emDamageResistanceBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 电磁伤害抗性加成",  # 电磁伤害抗性加成
            'shieldRechargeRateBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾回充速率加成",  # 护盾回充速率加成
            'shieldBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾加成",  # 护盾加成
            'shieldHPBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾值加成",  # 护盾值加成
            'armorHPBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 装甲值加成",  # 装甲值加成
            'structureHPBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 结构值加成",  # 结构值加成
            'armorHP': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机HP和跟踪速度加成",  # 装甲值
            'shieldCapacity': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机HP和跟踪速度加成",  # 护盾容量
            'structureHP': lambda bv, en: None,  # 跳过
            
            # 能量系统
            'EnergyTCapNeed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 小型能量炮台启动消耗减少",  # 小型能量炮台启动消耗
            'PropulsionJamming': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 推进抑制系统启动消耗减少",  # 推进抑制系统启动消耗
            'RemoteArmorRepairCapNeed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 远程装甲维修器启动消耗减少",  # 远程装甲维修器启动消耗
            'capacitorNeed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 小型能量炮台启动消耗减少" if 'EnergyTCapNeed' in en or 'SmallEnergy' in en or 'SETDmg' in en or ('AF' in en and 'Energy' in en) else f"{self._format_bonus_value(abs(bv))}% 中型能量炮台启动消耗减少" if 'MediumEnergy' in en or 'MEDmg' in en or ('AC' in en and 'Energy' in en) or ('ABC' in en and 'Energy' in en and not 'Large' in en) or 'shipLaserCapABC1' in en or 'shipLaserCapABC3' in en else f"{self._format_bonus_value(abs(bv))}% 大型能量炮台启动消耗减少" if 'LargeEnergy' in en or 'LEDmg' in en or ('ABC' in en and 'Large' in en) or ('ABS' in en and 'Energy' in en) or 'shipLargeLaserCapABC1' in en else f"{self._format_bonus_value(abs(bv))}% 小型混合炮台启动消耗减少" if 'SmallHybrid' in en else f"{self._format_bonus_value(abs(bv))}% 中型混合炮台启动消耗减少" if 'MediumHybrid' in en else f"{self._format_bonus_value(abs(bv))}% 大型混合炮台启动消耗减少" if 'LargeHybrid' in en else f"{self._format_bonus_value(abs(bv))}% 小型射弹炮台启动消耗减少" if 'SmallProjectile' in en else f"{self._format_bonus_value(abs(bv))}% 中型射弹炮台启动消耗减少" if 'MediumProjectile' in en else f"{self._format_bonus_value(abs(bv))}% 大型射弹炮台启动消耗减少" if 'LargeProjectile' in en else f"{self._format_bonus_value(abs(bv))}% 推进抑制系统启动消耗减少" if 'PropulsionJamming' in en or 'WarpScramble' in en or 'Webifier' in en else f"{self._format_bonus_value(abs(bv))}% 索敌扰断器启动消耗和CPU需求降低" if 'WeaponDisruption' in en or 'TD' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(abs(bv))}% 远程装甲维修器启动消耗减少" if 'RemoteArmorRepair' in en else f"{self._format_bonus_value(abs(bv))}% 能量中和器启动消耗减少" if 'EnergyNeutralizer' in en or 'Neutralizer' in en or 'shipNeutCap' in en else f"{self._format_bonus_value(abs(bv))}% 启动消耗减少",  # 启动消耗
            'capacitorNeedBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 电容需求降低",  # 电容需求
            'capacitorCapacityMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 电容容量加成",  # 电容量倍增系数
            'capacitorRechargeMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 电容回充速率加成",  # 电容回充倍增系数
            'capacitorRechargeRate': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 舰船电容回充速率加成",  # 电容回充时间
            'capacitorRechargeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 电容回充速度加成",  # 电容回充速度加成
            'powerOutputBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量输出加成",  # 能量输出加成
            
            # 武器系统
            'rateOfFireBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器射速加成",  # 射速加成
            'rateOfFire': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器射速加成",  # 射击速度
            'accuracyBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器准确性加成",  # 准确性加成
            'WeaponDisruptionMaxRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程加成",  # 武器扰断器最佳射程
            'TDOptimalBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程加成",  # 武器扰断器最佳射程
            'armorRepairProjectorMaxRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 远程装甲维修器最佳射程加成",  # 远程装甲维修器最佳射程
            'SPTOptimal': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'SmallEnergyTurretOptimal': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'SmallEnergyTurretOptimalRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'EMTOptimalBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'ETOptimalRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'Assault': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'Gunship': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台最佳射程加成",  # 小型能量炮台最佳射程
            'optimalRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'TD' in en or 'WeaponDisruption' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 最佳射程加成",  # 最佳射程
            'armorRepairProjectorFalloff': lambda bv, en: f"{self._format_bonus_value(bv)}% 远程装甲维修器失准范围加成",  # 远程装甲维修器失准范围
            'falloff': lambda bv, en: f"{self._format_bonus_value(bv)}% 远程装甲维修器失准范围加成" if 'armorRepairProjector' in en else f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'TD' in en or 'WeaponDisruption' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 效果失准范围加成",  # 效果失准范围
            'turretTrackingSpeedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 炮台跟踪速度加成",  # 炮台跟踪速度
            'missileRoFBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹射速加成",  # 导弹射速加成
            'turretRoFBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 炮台射速加成",  # 炮台射速加成
            'missileFlightTimeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹飞行时间加成",  # 导弹飞行时间加成
            'missileExplosionVelocityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹爆炸速度加成",  # 导弹爆炸速度加成
            'rangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器范围加成",  # 范围加成
            
            # 电子系统
            'maxTargetRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 锁定范围加成",  # 锁定范围上限
            'maxTargetRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 锁定范围加成",  # 锁定范围
            'scanSpeedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 扫描速度加成",  # 扫描速度
            'probeStrengthBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 探针强度加成",  # 探针强度加成
            'shipScanRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 舰船扫描范围加成",  # 舰船扫描范围
            'cargoScanRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 货柜扫描范围加成",  # 货柜扫描范围
            'ecmBurstRadiusBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% ECM脉冲半径加成",  # ECM脉冲半径
            
            # 机动性
            'speedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 速度加成",  # 速度加成
            'warpSpeedMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",  # 跃迁速度倍增系数
            'WarpSpeed': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",  # 跃迁速度倍增系数
            'roleBonusWarpSpeed': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",  # 跃迁速度倍增系数
            'signatureRadius': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低" if 'MWD' in en else f"{self._format_bonus_value(bv)}% 信号半径修正值",  # 信号半径修正值
            'signatureRadiusBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低" if 'Crusader' in en or 'Interceptor' in en else f"{self._format_bonus_value(bv)}% 信号半径加成",  # 信号半径加成
            'activeSignatureRadiusBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 主动信号半径加成",  # 主动信号半径加成
            'massAddition': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲附甲板质量惩罚减少" if 'ArmorPlateMassGC3' in en else f"{self._format_bonus_value(bv)}% 质量增加值",  # 质量增加值
            'Structure Hitpoints': lambda bv, en: None,  # 跳过，与无人机HP合并处理
            'Mining amount': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机采矿量加成",  # 采矿量
            'Maximum Velocity': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机最大速度加成",  # 最大速度
            'Shield Hitpoint Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾扩展装置护盾值加成",  # 护盾值加成
            'Structure Hitpoint Bonus': lambda bv, en: f"{int(bv * 100)}% 强化舱隔壁结构值加成",  # 结构值加成
            'Turret Tracking': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台跟踪速度加成" if 'AD' in en or 'AmarrDestroyer' in en else f"{self._format_bonus_value(bv)}% 中型能量炮台跟踪速度加成",  # 跟踪速度
            
            # 无人机
            'droneDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机伤害加成",  # 无人机伤害加成
            'droneTrackingBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机跟踪速度加成",  # 无人机跟踪速度加成
            'droneRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机射程加成",  # 无人机最佳射程和失准范围加成
            'droneWebBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机停滞缠绕光束加成",  # 无人机停滞缠绕光束加成
            
            # 其他
            'virusStrength': lambda bv, en: f"{int(bv)}＋ 遗迹分析仪和数据分析仪病毒强度加成",  # 病毒强度
            'entosisCPUAdd': lambda bv, en: None,  # 跳过负面效果
            'SalvageCycle': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 打捞装置运转周期降低",  # 打捞装置运转周期
            'duration': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 打捞装置运转周期降低" if 'SalvageCycle' in en else f"{self._format_bonus_value(bv)}% 作用时间/单次运转时间",  # 作用时间/单次运转时间
            'baseSensorStrength': lambda bv, en: f"{self._format_bonus_value(bv)}% 核心和作战扫描探针强度加成",  # 扫描强度基数
            'maxFlightTime': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 测量探针扫描时间减少",  # 最长飞行时间
            'SurveyProbeExplosionDelay': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 测量探针扫描时间减少",  # 最长飞行时间
            'covertOpsCloakCpu': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低",  # 隐形装置的CPU需求
            'WarpFactor': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低",  # 隐形装置的CPU需求
            'SurveyProbeLauncherCpuNeed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",  # 扫描探针发射器CPU需求
            'surveyProbeLauncher': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",  # 扫描探针发射器CPU需求
            'SurveyProbe': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",  # 扫描探针发射器CPU需求
            'ScanProbeLauncherCPU': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",  # 扫描探针发射器CPU需求
            'cpu': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低" if 'covertOpsCloakCpu' in en or 'WarpFactor' in en else f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低" if 'SurveyProbeLauncherCpuNeed' in en or 'surveyProbeLauncher' in en.lower() or 'SurveyProbe' in en or 'ScanProbeLauncherCPU' in en or 'ScanProbe' in en else f"{self._format_bonus_value(abs(bv))}% 索敌扰断器启动消耗和CPU需求降低" if 'WeaponDisruption' in en or 'TD' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(abs(bv))}% CPU需求降低",  # CPU需求
            'warpScrambleStrengthBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁干扰强度加成",  # 跃迁干扰强度加成
            'cycleTimeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 单次运转时间加成",  # 单次运转时间加成
            'miningCriticalChanceBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 采矿暴击成功率加成",  # 采矿暴击成功率加成
            'miningCriticalAmountBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 采矿暴击额外收益",  # 采矿成功暴击额外收益
            'wreckChanceReduction': lambda bv, en: f"{self._format_bonus_value(bv)}% 产生残渣几率降低",  # 产生残渣几率降低
            'specialAbilityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 特殊能力加成",  # 特殊能力加成
            'cloakStabilizationDurationBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 隐形稳定持续时间加成",  # 隐形稳定持续时间加成
            'passengerCapacityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 乘客数上限加成",  # 乘客数上限加成
            'range': lambda bv, en: f"{self._format_bonus_value(bv)}% 作用范围加成",  # 作用范围
            'moduleSelectionQuantityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 装备选择数量加成",  # 装备选择数量加成
            'moduleSelectionEventBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 装备选择事件加成",  # 装备选择事件加成
            'energyCoreBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量核心获得加成",  # 能量核心获得加成
            # 指挥脉冲波
            'commandBurstAoERoleBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 指挥脉冲波效果范围加成",  # 指挥脉冲波效果范围
            'battlecruiserDroneSpeed': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机最大速度加成",  # 无人机最大速度
            'battlecruiserMETRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程和失准范围加成",  # 中型能量炮台最佳射程和失准范围
            'battlecruiserMETRange2': lambda bv, en: f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程和失准范围加成",  # 中型能量炮台最佳射程和失准范围
            'bcLargeEnergyTurretCPUNeedBonus': lambda bv, en: f"{self._format_bonus_value(bv * 100)}% 大型能量炮台CPU需求降低",  # 大型能量炮台CPU需求
            'bcLargeEnergyTurretCapacitorNeedBonus': lambda bv, en: f"{self._format_bonus_value(bv * 100)}% 大型能量炮台启动消耗减少",  # 大型能量炮台启动消耗
            'BattleshipRoleBonusArmorPlate&ShieldExtenderHP': lambda bv, en: None,  # 跳过，单独处理
            'capacityBonus': lambda bv, en: None,  # 跳过，与护盾值合并处理
            'structureHPMultiplier': lambda bv, en: None,  # 跳过，与结构值合并处理
            # 新增的条目
            'MWDSignatureRadiusRoleBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低",
            'emDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 炸弹电磁伤害加成" if 'Bomb' in en else f"{self._format_bonus_value(bv)}% 鱼雷电磁伤害加成" if 'Torpedo' in en else f"{self._format_bonus_value(bv)}% 电磁伤害加成",
            'thermalDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 热能伤害加成",
            'kineticDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 动能伤害加成",
            'explosiveDamage': lambda bv, en: None if 'shipBonusTorpedoDamageAB' in en or 'shipBonusCruiseMissileDamageAB' in en or 'shipBonusHeavyMissileDamageAB' in en else f"{self._format_bonus_value(bv)}% 爆炸伤害加成",
            'Rate of fire': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台伤害加成" if 'SmallEnergy' in en or 'SETDmg' in en or 'Retribution' in en else f"{self._format_bonus_value(bv)}% 中型能量炮台伤害加成" if 'MediumEnergy' in en or 'MEDmg' in en else f"{self._format_bonus_value(bv)}% 大型能量炮台伤害加成" if 'LargeEnergy' in en or 'LEDmg' in en else f"{self._format_bonus_value(bv)}% 武器射速加成",
            'speed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 小型能量炮台射速加成" if 'Retribution' in en or 'shipSETROFAF' in en or ('AF' in en and 'Energy' in en) else f"{self._format_bonus_value(abs(bv))}% 中型能量炮台射速加成" if ('AC' in en and 'Energy' in en or ('ABC' in en and 'Energy' in en and not 'Large' in en)) else f"{self._format_bonus_value(abs(bv))}% 大型能量炮台射速加成" if ('ABC' in en and 'Large' in en) or ('ABS' in en and 'Energy' in en) else f"{self._format_bonus_value(abs(bv))}% 火箭和轻型导弹发射器射速加成" if 'shipMissileSpeedBonusAF' in en or 'Vengeance' in en else f"{self._format_bonus_value(bv)}% 速度加成",
            'turretTrackingSpeedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 炮台跟踪速度加成",
            'missileRoFBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹射速加成",
            'turretRoFBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 炮台射速加成",
            'missileFlightTimeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹飞行时间加成",
            'rangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器范围加成",
            'maxTargetRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 锁定范围加成",
            'maxTargetRange': lambda bv, en: f"{self._format_bonus_value(bv)}% 锁定范围加成",
            'scanSpeedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 扫描速度加成",
            'probeStrengthBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 探针强度加成",
            'shipScanRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 舰船扫描范围加成",
            'cargoScanRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 货柜扫描范围加成",
            'ecmBurstRadiusBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% ECM脉冲半径加成",
            'warpSpeedMultiplier': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",
            'WarpSpeed': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",
            'roleBonusWarpSpeed': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁速度和跃迁加速加成",
            'signatureRadius': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低" if 'MWD' in en else f"{self._format_bonus_value(bv)}% 信号半径修正值",
            'signatureRadiusBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 微型跃迁推进器的信号半径惩罚降低" if 'Crusader' in en or 'Interceptor' in en else f"{self._format_bonus_value(bv)}% 信号半径加成",
            'activeSignatureRadiusBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 主动信号半径加成",
            'massAddition': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 装甲附甲板质量惩罚减少" if 'ArmorPlateMassGC3' in en else f"{self._format_bonus_value(bv)}% 质量增加值",
            'droneDamageBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机伤害加成",
            'droneTrackingBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机跟踪速度加成",
            'droneRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机射程加成",
            'droneWebBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机停滞缠绕光束加成",
            'virusStrength': lambda bv, en: f"{int(bv)}＋ 遗迹分析仪和数据分析仪病毒强度加成",
            'entosisCPUAdd': lambda bv, en: None,
            'SalvageCycle': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 打捞装置运转周期降低",
            'duration': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 打捞装置运转周期降低" if 'SalvageCycle' in en else f"{self._format_bonus_value(bv)}% 作用时间/单次运转时间",
            'baseSensorStrength': lambda bv, en: f"{self._format_bonus_value(bv)}% 核心和作战扫描探针强度加成",
            'maxFlightTime': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 测量探针扫描时间减少",
            'SurveyProbeExplosionDelay': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 测量探针扫描时间减少",
            'covertOpsCloakCpu': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低",
            'WarpFactor': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低",
            'SurveyProbeLauncherCpuNeed': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",
            'surveyProbeLauncher': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",
            'SurveyProbe': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",
            'ScanProbeLauncherCPU': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低",
            'cpu': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 隐形装置的CPU需求降低" if 'covertOpsCloakCpu' in en or 'WarpFactor' in en else f"{self._format_bonus_value(abs(bv))}% 扫描探针发射器CPU需求降低" if 'SurveyProbeLauncherCpuNeed' in en or 'surveyProbeLauncher' in en.lower() or 'SurveyProbe' in en or 'ScanProbeLauncherCPU' in en or 'ScanProbe' in en else f"{self._format_bonus_value(abs(bv))}% 推进抑制系统启动消耗减少" if 'Crusader' in en or 'Interceptor' in en else f"{self._format_bonus_value(abs(bv))}% 索敌扰断器启动消耗和CPU需求降低" if 'WeaponDisruption' in en or 'TD' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(abs(bv))}% CPU需求降低",
            'warpScrambleStrengthBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 跃迁干扰强度加成",
            'shipLaserRofAC2': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 中型能量炮台射速加成",
            'shipBonusEwWeaponDisruptionStrengthAC1': lambda bv, en: f"{self._format_bonus_value(bv)}% 武器扰断器效果加成",
            'shipBonusMETOptimalAC2': lambda bv, en: f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程加成",
            'shipBonusMediumEnergyWeaponRangeABC1': lambda bv, en: f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程加成",
            'shipBonusLargeEnergyTurretMaxRangeAB2': lambda bv, en: f"{self._format_bonus_value(bv)}% 大型能量炮台最佳射程加成",
            'shipBonusEnergyNeutOptimalAB': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNeutFalloffAB2': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'shipBonusEnergyNosOptimalAB': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNosFalloffAB2': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'shipBonusEnergyNeutOptimalAD1': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNosOptimalAD2': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNeutFalloffAD1': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'shipBonusEnergyNosFalloffAD1': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'shipBonusTorpedoDamageAB': lambda bv, en: f"{self._format_bonus_value(bv)}% 鱼雷伤害加成",
            'shipBonusCruiseMissileDamageAB': lambda bv, en: f"{self._format_bonus_value(bv)}% 巡航导弹伤害加成",
            'shipBonusHeavyMissileDamageAB': lambda bv, en: f"{self._format_bonus_value(bv)}% 重型导弹伤害加成",
            'bcLargeEnergyTurretCPUNeedBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 大型能量炮台CPU需求降低",
            'bcLargeEnergyTurretCapacitorNeedBonus': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 大型能量炮台启动消耗减少",
            'falloff': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成" if 'shipBonusEnergyNeutFalloffAB2' in en or 'shipBonusEnergyNosFalloffAB2' in en or 'shipBonusEnergyNeutFalloffAD1' in en or 'shipBonusEnergyNosFalloffAD1' in en else f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程和失准范围加成" if 'battlecruiserMETRange' in en or 'battlecruiserMETRange2' in en else f"{self._format_bonus_value(bv)}% 远程装甲维修器失准范围加成" if 'armorRepairProjector' in en else f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'TD' in en or 'WeaponDisruption' in en or 'Maller' in en or 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 效果失准范围加成",
            'eliteBonusGunshipCapRecharge2': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 小型能量炮台伤害加成",
            'covertOpsCpuBonus1': lambda bv, en: f"{self._format_bonus_value(bv * 10)}% 鱼雷伤害加成",
            'shipBonusEnergyNeutOptimalEAF1': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNeutFalloffEAF3': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'shipBonusEnergyNosOptimalEAF1': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成",
            'shipBonusEnergyNosFalloffEAF3': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器失准范围加成",
            'eliteBonusElectronicAttackShipRechargeRate2': lambda bv, en: f"{self._format_bonus_value(abs(bv))}% 电容回充速率加成",
            'TrackingSpeedBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 跟踪速度加成",
            'ExplosionRadiusBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸半径加成",
            'ExplosionVelocityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸速度加成",
            'FlightTimeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 飞行时间加成",
            'MissileVelocityBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹速度加成",
            'OptimalRangeBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 最佳射程加成",
            'Tracking Speed Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 跟踪速度加成",
            'Optimal Range Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 最佳射程加成",
            'Explosion Radius Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸半径加成",
            'Explosion Velocity Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 爆炸速度加成",
            'Flight Time Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 飞行时间加成",
            'Missile Velocity Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 导弹速度加成",
            'Optimal Range': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器和能量中和器最佳射程加成" if 'shipBonusEnergyNeutOptimalAB' in en or 'shipBonusEnergyNosOptimalAB' in en or 'shipBonusEnergyNeutOptimalAD1' in en or 'shipBonusEnergyNosOptimalAD2' in en else f"{self._format_bonus_value(bv)}% 大型能量炮台最佳射程加成" if 'shipBonusLargeEnergyTurretMaxRangeAB2' in en else f"{self._format_bonus_value(bv)}% 中型能量炮台最佳射程加成" if 'shipBonusMETOptimalAC2' in en or 'shipBonusMediumEnergyWeaponRangeABC1' in en else f"{self._format_bonus_value(bv)}% 武器扰断器最佳射程和失准范围惩罚" if 'Crucifier' in en else f"{self._format_bonus_value(bv)}% 最佳射程",
            'Structure Hitpoints': lambda bv, en: None,
            'Mining amount': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机采矿量加成",
            'Maximum Velocity': lambda bv, en: f"{self._format_bonus_value(bv)}% 无人机最大速度加成",
            'Shield Hitpoint Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 护盾扩展装置护盾值加成",
            'Structure Hitpoint Bonus': lambda bv, en: f"{int(bv * 100)}% 强化舱隔壁结构值加成",
            'Turret Tracking': lambda bv, en: f"{self._format_bonus_value(bv)}% 小型能量炮台跟踪速度加成" if 'AD' in en or 'AmarrDestroyer' in en else f"{self._format_bonus_value(bv)}% 中型能量炮台跟踪速度加成",
            'energyNeutralizerAmountBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量中和器吸取量加成",
            'energyNosferatuAmountBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器吸取量加成",
            'EnergyNeutralizerTransferAmount': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量中和器吸取量加成",
            'shipEnergyDrainAmount': lambda bv, en: f"{self._format_bonus_value(bv)}% 掠能器吸取量加成",
            'shipEnergyNeutralizerTransferAmountBonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量中和器吸取量加成",
            'shipEnergyNeutralizerTransferAmountBonusAF': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量中和器吸取量加成",
            'shipEnergyNeutralizerTransferAmountBonusAmaNavyDestroyer': lambda bv, en: f"{self._format_bonus_value(bv)}% 能量中和器强度加成",
            'Overload Speed Bonus': lambda bv, en: f"{self._format_bonus_value(bv)}% 加力燃烧器和微型跃迁推进器过载效果加成",
        }
        
        # 初始化技能类型映射字典
        self.skill_type_map = {
            # 护卫舰
            'GF': '盖伦特护卫舰',
            'GallenteFrigate': '盖伦特护卫舰',
            'AF': '艾玛护卫舰',
            'A2F': '艾玛护卫舰',
            'A3F': '艾玛护卫舰',
            'AmarrFrigate': '艾玛护卫舰',
            'MF': '米玛塔尔护卫舰',
            'M2F': '米玛塔尔护卫舰',
            'M3F': '米玛塔尔护卫舰',
            'MinmatarFrigate': '米玛塔尔护卫舰',
            'CF': '加达里护卫舰',
            'C2F': '加达里护卫舰',
            'C3F': '加达里护卫舰',
            'CaldariFrigate': '加达里护卫舰',
            # 驱逐舰
            'GD': '盖伦特驱逐舰',
            'G2D': '盖伦特驱逐舰',
            'G3D': '盖伦特驱逐舰',
            'GallenteDestroyer': '盖伦特驱逐舰',
            'AD': '艾玛驱逐舰',
            'A2D': '艾玛驱逐舰',
            'A3D': '艾玛驱逐舰',
            'AmarrDestroyer': '艾玛驱逐舰',
            'MD': '米玛塔尔驱逐舰',
            'M2D': '米玛塔尔驱逐舰',
            'M3D': '米玛塔尔驱逐舰',
            'MinmatarDestroyer': '米玛塔尔驱逐舰',
            'CD': '加达里驱逐舰',
            'C2D': '加达里驱逐舰',
            'C3D': '加达里驱逐舰',
            'CaldariDestroyer': '加达里驱逐舰',
            # 巡洋舰
            'GC': '盖伦特巡洋舰',
            'G2C': '盖伦特巡洋舰',
            'G3C': '盖伦特巡洋舰',
            'GallenteCruiser': '盖伦特巡洋舰',
            'AC': '艾玛巡洋舰',
            'A2C': '艾玛巡洋舰',
            'A3C': '艾玛巡洋舰',
            'AmarrCruiser': '艾玛巡洋舰',
            'MC': '米玛塔尔巡洋舰',
            'M2C': '米玛塔尔巡洋舰',
            'M3C': '米玛塔尔巡洋舰',
            'MinmatarCruiser': '米玛塔尔巡洋舰',
            'CC': '加达里巡洋舰',
            'C2C': '加达里巡洋舰',
            'C3C': '加达里巡洋舰',
            'CaldariCruiser': '加达里巡洋舰',
            # 战列巡洋舰
            'GBC': '盖伦特战列巡洋舰',
            'G2BC': '盖伦特战列巡洋舰',
            'G3BC': '盖伦特战列巡洋舰',
            'GallenteBattlecruiser': '盖伦特战列巡洋舰',
            'ABC': '艾玛战列巡洋舰',
            'A2BC': '艾玛战列巡洋舰',
            'A3BC': '艾玛战列巡洋舰',
            'AmarrBattlecruiser': '艾玛战列巡洋舰',
            'MBC': '米玛塔尔战列巡洋舰',
            'M2BC': '米玛塔尔战列巡洋舰',
            'M3BC': '米玛塔尔战列巡洋舰',
            'MinmatarBattlecruiser': '米玛塔尔战列巡洋舰',
            'CBC': '加达里战列巡洋舰',
            'C2BC': '加达里战列巡洋舰',
            'C3BC': '加达里战列巡洋舰',
            'CaldariBattlecruiser': '加达里战列巡洋舰',
            # 战列舰
            'GBS': '盖伦特战列舰',
            'G2BS': '盖伦特战列舰',
            'G3BS': '盖伦特战列舰',
            'GallenteBattleship': '盖伦特战列舰',
            'AB': '艾玛战列舰',
            'ABS': '艾玛战列舰',
            'A2BS': '艾玛战列舰',
            'A3BS': '艾玛战列舰',
            'AmarrBattleship': '艾玛战列舰',
            'MBS': '米玛塔尔战列舰',
            'M2BS': '米玛塔尔战列舰',
            'M3BS': '米玛塔尔战列舰',
            'MinmatarBattleship': '米玛塔尔战列舰',
            'CBS': '加达里战列舰',
            'C2BS': '加达里战列舰',
            'C3BS': '加达里战列舰',
            'CaldariBattleship': '加达里战列舰',
            # 其他舰船类型
            'Supercarrier': '超级航母',
            'Titan': '泰坦',
            'Dreadnought': '无畏舰',
            'Carrier': '航母',
            'ForceAuxiliary': '战力辅助舰',
            'CovertOps': '隐形特勤舰',
            'eliteBonusCoverOps': '隐形特勤舰',
            'covertOpsWarpResistance': '隐形特勤舰',
            'covertOpsCloakCpuPercentBonus1': '隐形特勤舰',
            'shipBonusSurveyProbeExplosionDelaySkillSurveyCovertOps3': '隐形特勤舰',
            'Interceptor': '截击舰',
            'interceptor': '截击舰',
            'interceptor2LaserTracking': '截击舰',
            'interceptorMWDSignatureRadiusBonus': '截击舰',
            'Interceptor2WarpScrambleRange': '截击舰',
            'AssaultFrigate': '突击护卫舰',
            'assaultfrigate': '突击护卫舰',
            'Assault': '突击护卫舰',
            'assault': '突击护卫舰',
            'assaultShipBonus': '突击护卫舰',
            'AssaultShip': '突击护卫舰',
            'HeavyAssault': '重型突击舰',
            'heavyAssault': '重型突击舰',
            'HeavyGunship': '重型突击舰',
            'heavyGunship': '重型突击舰',
            'eliteBonusHeavyGunship': '重型突击舰',
            'Gunship': '突击护卫舰',
            'gunship': '突击护卫舰',
            'eliteBonusGunship': '突击护卫舰',
            'ElectronicAttackShip': '电子攻击舰',
            'EAF': '电子攻击舰',
            'eliteBonusElectronicAttackShip': '电子攻击舰',
            'eliteBonusElectronicAttackShipRechargeRate2': '电子攻击舰',
            # 特殊效果名称
            'shipBonusDroneHitpointsGF': '盖伦特护卫舰',
            'shipArmor': '艾玛护卫舰',
            'shipRocketRoFBonusAF2': '艾玛护卫舰',
            'shipHTDmgBonusfixedGC': '艾玛护卫舰',
            'shipBonusHybridFalloffGC2': '艾玛护卫舰',
            'shipBonusArmorPlateMassGC3': '艾玛护卫舰'
        }

    async def initialize(self):
        """插件初始化方法"""
        logger.info("EVE ESI 插件初始化")
        # 创建aiohttp ClientSession
        self.session = aiohttp.ClientSession()

    async def shutdown(self):
        """插件关闭方法"""
        logger.info("EVE ESI 插件关闭")
        # 关闭aiohttp ClientSession
        if self.session:
            await self.session.close()

    def _load_aliases(self):
        """加载简称字典"""
        try:
            if os.path.exists(self.alias_file):
                with open(self.alias_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载简称字典失败: {e}")
        return {}

    def _save_aliases(self):
        """保存简称字典"""
        try:
            with open(self.alias_file, 'w', encoding='utf-8') as f:
                json.dump(self.aliases, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存简称字典失败: {e}")

    @filter.command("简称")
    async def add_alias(self, event: AstrMessageEvent):
        """添加简称"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /简称 <全称>=<简称>")
            return
        
        alias_part = " ".join(parts[1:])
        if "=" not in alias_part:
            yield event.plain_result("使用方法: /简称 <全称>=<简称>")
            return
        
        full_name, alias = alias_part.split("=", 1)
        full_name = full_name.strip()
        alias = alias.strip()
        
        if not full_name or not alias:
            yield event.plain_result("全称和简称不能为空")
            return
        
        # 添加简称
        if full_name not in self.aliases:
            self.aliases[full_name] = []
        
        if alias not in self.aliases[full_name]:
            self.aliases[full_name].append(alias)
            self._save_aliases()
            yield event.plain_result(f"已添加简称: {alias} -> {full_name}")
        else:
            yield event.plain_result(f"简称 {alias} 已存在")

    @filter.command("简称列表")
    async def list_alias(self, event: AstrMessageEvent):
        """查看简称列表"""
        message_str = event.message_str
        parts = message_str.split(" ")
        
        if len(parts) < 2:
            # 显示所有简称
            if not self.aliases:
                yield event.plain_result("暂无简称")
                return
            
            result = "简称列表:\n"
            for full_name, aliases in self.aliases.items():
                result += f"{full_name}: {', '.join(aliases)}\n"
            yield event.plain_result(result)
        else:
            # 查询指定全称或简称
            query = " ".join(parts[1:])
            
            # 检查是否是简称
            for full_name, aliases in self.aliases.items():
                if query in aliases:
                    yield event.plain_result(f"{full_name}: {', '.join(aliases)}")
                    return
            
            # 检查是否是全称
            if query in self.aliases:
                yield event.plain_result(f"{query}: {', '.join(self.aliases[query])}")
            else:
                yield event.plain_result(f"{query} 还没有简称")

    @filter.command("简称删除")
    async def delete_alias(self, event: AstrMessageEvent):
        """删除简称"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /简称删除 <简称>")
            return
        
        alias = " ".join(parts[1:])
        
        # 查找并删除简称
        found = False
        for full_name, aliases in list(self.aliases.items()):
            if alias in aliases:
                aliases.remove(alias)
                if not aliases:
                    del self.aliases[full_name]
                found = True
                break
        
        if found:
            self._save_aliases()
            yield event.plain_result(f"已删除简称: {alias}")
        else:
            yield event.plain_result(f"简称 {alias} 不存在")

    @filter.command("jt")
    async def get_jita_price_short(self, event: AstrMessageEvent):
        """查询吉他市场价格（短命令）"""
        async for result in self.get_jita_price(event):
            yield result

    @filter.command("吉他")
    async def get_jita_price(self, event: AstrMessageEvent):
        """查询吉他市场价格"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /吉他 <物品名称或ID>")
            return
        
        query = " ".join(parts[1:])
        # 检查是否有简称
        if query in self.aliases:
            logger.info(f"使用简称: {query} -> {self.aliases[query]}")
            query = self.aliases[query]
        
        # 尝试将查询转换为数字（物品ID）
        if query.isdigit():
            item_id = query
            async for result in self._get_jita_price(item_id, event, ''):
                yield result
        else:
            # 先使用市场中心API搜索，将名称转换为ID
            logger.info(f"使用市场中心API搜索物品: {query}")
            market_result = await self.search_item_by_name(query)
            
            if market_result and len(market_result) > 0:
                # 判断用户是否明确查询涂装或蓝图
                is_query_skin = self._is_skin(query)
                is_query_blueprint = self._is_blueprint(query)
                
                if is_query_skin or is_query_blueprint:
                    # 用户明确查询涂装或蓝图，不过滤，显示所有结果
                    result_text = f"找到'{query}'的搜索结果:\n"
                    for i, item in enumerate(market_result[:10], 1):  # 显示前10个结果
                        result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                    result_text += "\n请使用 /吉他 <物品ID> 查看具体物品价格"
                    yield event.plain_result(result_text)
                else:
                    # 用户查询的是缩写，过滤掉涂装和蓝图
                    filtered_result = [
                        item for item in market_result 
                        if not self._is_skin(item.get('typename', '')) 
                        and not self._is_blueprint(item.get('typename', ''))
                    ]
                    skin_count = len(market_result) - len(filtered_result)
                    
                    if filtered_result:
                        # 构建结果文本
                        result_text = f"找到'{query}'的{len(market_result)}个结果\n"
                        if skin_count > 0:
                            result_text += f"其中{skin_count}个为涂装或蓝图暂时过滤\n"
                        result_text += "\n"
                        
                        # 显示前5个的具体价格（卖价在前，买价在后）
                        for i, item in enumerate(filtered_result[:5], 1):
                            item_id = str(item.get('typeid', ''))
                            item_name = item.get('typename', '未知')
                            buy_price, sell_price = await self._get_item_price_info(item_id)
                            result_text += f"{i}. {item_name}\n"
                            result_text += f"   {sell_price}\n"
                            result_text += f"   {buy_price}\n\n"
                        
                        # 如果超过5个，列出剩下的全名
                        if len(filtered_result) > 5:
                            remaining_count = len(filtered_result) - 5
                            result_text += f"其他结果（还有{remaining_count}个）:\n"
                            for i, item in enumerate(filtered_result[5:10], 6):
                                result_text += f"{i}. {item.get('typename', '未知')}\n"
                            if remaining_count > 5:
                                result_text += f"... 还有 {remaining_count - 5} 个结果\n"
                            result_text += "\n"
                        
                        result_text += "如果你的搜索物品不在本列表内，请再详细一点搜索"
                        yield event.plain_result(result_text)
                    else:
                        # 如果过滤后没有结果，提示用户
                        yield event.plain_result("搜索结果前十个都是涂装或蓝图。请再详细一点搜索。")
            else:
                # 如果市场中心没找到，提示用户使用更详细的名称或物品ID
                yield event.plain_result(f"未找到物品'{query}'。提示：请使用更详细的中文名称或物品ID（如34）进行查询。")

    @filter.command("属性")
    async def get_item_info_short(self, event: AstrMessageEvent):
        """查看物品具体信息"""
        message_str = event.message_str
        parts = message_str.split(" ")
        if len(parts) < 2:
            yield event.plain_result("使用方法: /属性 <物品名称或ID>")
            return
        
        query = " ".join(parts[1:])
        # 检查是否有简称
        if query in self.aliases:
            logger.info(f"使用简称: {query} -> {self.aliases[query]}")
            query = self.aliases[query]
        
        # 尝试将查询转换为数字（物品ID）
        if query.isdigit():
            item_id = query
            async for result in self._get_item_info(item_id, event, ''):
                yield result
        else:
            # 先使用市场中心API搜索，将名称转换为ID
            logger.info(f"使用市场中心API搜索物品: {query}")
            market_result = await self.search_item_by_name(query)
            
            if market_result and len(market_result) > 0:
                # 判断用户是否明确查询涂装或蓝图
                is_query_skin = self._is_skin(query)
                is_query_blueprint = self._is_blueprint(query)
                
                if is_query_skin or is_query_blueprint:
                    # 用户明确查询涂装或蓝图，不过滤，显示所有结果
                    result_text = f"找到'{query}'的搜索结果:\n"
                    for i, item in enumerate(market_result[:10], 1):  # 显示前10个结果
                        result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                    result_text += "\n请使用 /属性 <物品ID> 查看具体物品信息"
                    yield event.plain_result(result_text)
                else:
                    # 用户查询的是缩写，过滤掉涂装和蓝图
                    filtered_result = [
                        item for item in market_result 
                        if not self._is_skin(item.get('typename', '')) 
                        and not self._is_blueprint(item.get('typename', ''))
                    ]
                    
                    if filtered_result:
                        if len(filtered_result) == 1:
                            # 只有一个结果，直接显示物品信息
                            item = filtered_result[0]
                            item_id = str(item.get('typeid', ''))
                            item_name = item.get('typename', '')
                            async for result in self._get_item_info(item_id, event, item_name):
                                yield result
                        else:
                            # 多个结果，列出所有非涂装非蓝图结果，引导用户再用命令查看
                            result_text = f"找到'{query}'的{len(filtered_result)}个结果:\n"
                            for i, item in enumerate(filtered_result[:15], 1):  # 显示前15个结果
                                result_text += f"{i}. {item.get('typename', '未知')} (ID: {item.get('typeid', '未知')})\n"
                            if len(filtered_result) > 15:
                                result_text += f"... 还有 {len(filtered_result) - 15} 个结果\n"
                            result_text += "\n请使用 /属性 <物品ID> 查看具体物品信息"
                            yield event.plain_result(result_text)
                    else:
                        # 如果过滤后没有结果，提示用户
                        yield event.plain_result("搜索结果前十个都是涂装或蓝图。请再详细一点搜索。")
            else:
                # 如果市场中心没找到，提示用户使用更详细的名称或物品ID
                yield event.plain_result(f"未找到物品'{query}'。提示：请使用更详细的中文名称或物品ID（如34）进行查询。")

    def _format_price(self, price):
        """格式化价格显示
        
        格式：数字（简化表示）
        例如：1,234,567,890.12 (12亿) 或 12,345,678.90 (1234万)
        """
        if price is None or price == 0:
            return "0"
        
        # 千分位格式化
        formatted = f"{price:,.2f}"
        
        # 计算简化表示，只显示亿和万
        if price >= 100000000:  # 1亿以上
            simplified = price / 100000000
            if simplified >= 100:
                return f"{formatted} ({int(simplified)}亿)"
            else:
                return f"{formatted} ({simplified:.1f}亿)"
        elif price >= 10000:  # 1万以上
            simplified = price / 10000
            return f"{formatted} ({int(simplified)}万)"
        else:
            return formatted

    async def _get_item_price_info(self, item_id):
        """获取物品简要价格信息（用于多结果列表显示）"""
        jita_region_id = 10000002
        
        try:
            # 获取买单和卖单
            buy_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=buy")
            sell_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=sell")
            
            # 计算最高买单和最低卖单
            highest_buy = 0
            if buy_orders and len(buy_orders) > 0:
                highest_buy = max(order['price'] for order in buy_orders)
            
            lowest_sell = 0
            if sell_orders and len(sell_orders) > 0:
                lowest_sell = min(order['price'] for order in sell_orders)
            
            # 格式化价格
            buy_text = f"买:{self._format_price(highest_buy)}" if highest_buy > 0 else "买:无数据"
            sell_text = f"卖:{self._format_price(lowest_sell)}" if lowest_sell > 0 else "卖:无数据"
            
            return buy_text, sell_text
        except Exception as e:
            logger.error(f"获取物品价格信息失败: {e}")
            return "买:错误", "卖:错误"

    async def _get_jita_price(self, item_id, event, item_name_cn=''):
        """获取吉他市场价格的内部方法"""
        # PLEX（伊甸币）的物品ID列表
        plex_ids = ['50001', '44992']  # 50001是国服PLEX ID，44992是国际服PLEX ID
        
        if item_id in plex_ids:
            # PLEX有专门的市场端点
            async for result in self._get_plex_price(item_id, event, item_name_cn):
                yield result
            return
        
        jita_region_id = 10000002  # 吉他所在的区域ID
        # 获取吉他市场价格
        buy_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=buy")
        sell_orders = await self.esi_request(f"/v1/markets/{jita_region_id}/orders/?type_id={item_id}&order_type=sell")
        
        # 获取物品信息
        item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
        item_name = item_name_cn if item_name_cn else (item_info.get('name', '未知物品') if item_info else '未知物品')
        
        # 处理买单数据
        highest_buy = 0
        buy_volume = 0
        if buy_orders and len(buy_orders) > 0:
            # 按价格排序，获取最高买单
            buy_orders.sort(key=lambda x: x['price'], reverse=True)
            highest_buy = buy_orders[0]['price']
            buy_volume = sum(order['volume_remain'] for order in buy_orders[:5])  # 前5个买单的数量
        
        # 处理卖单数据
        lowest_sell = 0
        sell_volume = 0
        if sell_orders and len(sell_orders) > 0:
            # 按价格排序，获取最低卖单
            sell_orders.sort(key=lambda x: x['price'])
            lowest_sell = sell_orders[0]['price']
            sell_volume = sum(order['volume_remain'] for order in sell_orders[:5])  # 前5个卖单的数量
        
        # 构建结果文本
        result = f"吉他市场价格信息:\n"
        result += f"物品名: {item_name}\n"
        result += f"物品ID: {item_id}\n"
        
        if highest_buy > 0:
            result += f"最高买单: {self._format_price(highest_buy)} (数量: {buy_volume})\n"
        else:
            result += "最高买单: 无数据\n"
        
        if lowest_sell > 0:
            result += f"最低卖单: {self._format_price(lowest_sell)} (数量: {sell_volume})\n"
        else:
            result += "最低卖单: 无数据\n"
        
        yield event.plain_result(result)

    async def _get_plex_price(self, item_id, event, item_name_cn=''):
        """获取伊甸币价格（国服特殊处理）"""
        yield event.plain_result("伊甸币价格查询暂不可用")
    
    def _extract_attributes(self, item_info):
        """提取物品属性"""
        attr_dict = {}
        for attr in item_info.get('dogma_attributes', []):
            attr_dict[attr['attribute_id']] = attr['value']
        return attr_dict
    
    async def _process_bonuses(self, dogma_effects, attr_dict, session=None, item_name=''):
        """处理技能加成和特有加成"""
        # 导入re模块
        import re
        
        # 初始化所有技能加成容器
        skill_bonuses_dict = {
            '盖伦特护卫舰': [],
            '艾玛护卫舰': [],
            '米玛塔尔护卫舰': [],
            '加达里护卫舰': [],
            '盖伦特驱逐舰': [],
            '艾玛驱逐舰': [],
            '米玛塔尔驱逐舰': [],
            '加达里驱逐舰': [],
            '盖伦特巡洋舰': [],
            '艾玛巡洋舰': [],
            '米玛塔尔巡洋舰': [],
            '加达里巡洋舰': [],
            '盖伦特战列巡洋舰': [],
            '艾玛战列巡洋舰': [],
            '米玛塔尔战列巡洋舰': [],
            '加达里战列巡洋舰': [],
            '盖伦特战列舰': [],
            '艾玛战列舰': [],
            '米玛塔尔战列舰': [],
            '加达里战列舰': [],
            '超级航母': [],
            '泰坦': [],
            '无畏舰': [],
            '航母': [],
            '战力辅助舰': [],
            '隐形特勤舰': [],
            '截击舰': [],
            '突击护卫舰': [],
            '重型突击舰': [],
            '电子攻击舰': [],
        }
        unique_bonuses = []
        
        # 跟踪装甲、护盾抗性和武器扰断器效果加成
        armor_resistance_bonuses = {}
        shield_resistance_bonuses = {}
        weapon_disruption_bonuses = {}
        missile_damage_bonuses = {}
        
        # 检查是否是科洛斯级或刽子手级或咒灭级
        is_crusader = 'Crusader' in item_name
        is_malediction = 'Malediction' in item_name
        is_executioner = 'Executioner' in item_name
        
        for effect in dogma_effects:
            effect_id = effect.get('effect_id')
            effect_info = await self.esi_request(f"/v1/dogma/effects/{effect_id}/")
            if effect_info:
                effect_name = effect_info.get('name', '')
                modifiers = effect_info.get('modifiers', [])
                
                # 解析加成信息
                bonus_texts = await self._process_modifiers(modifiers, attr_dict, effect_name)
                
                # 去重
                bonus_texts = list(dict.fromkeys(bonus_texts))
                
                # 特殊处理：武器扰断器效果加成只保留一个
                if 'shipBonusEwWeaponDisruptionStrengthAC1' in effect_name:
                    # 只保留武器扰断器效果加成
                    new_bonus_texts = []
                    for bonus_text in bonus_texts:
                        if '武器扰断器效果加成' in bonus_text:
                            new_bonus_texts.append(bonus_text)
                            break
                    bonus_texts = new_bonus_texts
                
                # 特殊处理：无人机HP和跟踪速度加成只保留一个，过滤掉Structure Hitpoints
                if 'shipBonusDroneHitpointsFixedAC2' in effect_name or 'shipBonusDroneHitpointsABC2' in effect_name or 'shipBonusDroneStructureHitPointsAB' in effect_name or 'shipBonusDroneHitpointsAB' in effect_name or 'shipBonusDroneHitpointsAD1' in effect_name:
                    # 只保留无人机HP和跟踪速度加成
                    new_bonus_texts = []
                    for bonus_text in bonus_texts:
                        if '无人机HP和跟踪速度加成' in bonus_text:
                            new_bonus_texts.append(bonus_text)
                            break
                    bonus_texts = new_bonus_texts
                
                # 特殊处理：导弹伤害加成只保留一个
                if 'shipBonusTorpedoDamageAB' in effect_name or 'shipBonusCruiseMissileDamageAB' in effect_name or 'shipBonusHeavyMissileDamageAB' in effect_name:
                    # 只保留对应的导弹伤害加成
                    new_bonus_texts = []
                    for bonus_text in bonus_texts:
                        if '鱼雷伤害加成' in bonus_text or '巡航导弹伤害加成' in bonus_text or '重型导弹伤害加成' in bonus_text:
                            new_bonus_texts.append(bonus_text)
                            break
                    bonus_texts = new_bonus_texts
                
                if bonus_texts:
                    # 识别技能类型
                    skill_type = self._identify_skill_type(effect_name)
                    
                    if skill_type and skill_type in skill_bonuses_dict:
                        # 技能加成
                        for bonus_text in bonus_texts:
                            if '装甲电磁伤害抗性' in bonus_text or '装甲热能伤害抗性' in bonus_text or '装甲动能伤害抗性' in bonus_text or '装甲爆炸伤害抗性' in bonus_text:
                                # 收集装甲抗性加成
                                if skill_type not in armor_resistance_bonuses:
                                    armor_resistance_bonuses[skill_type] = []
                                armor_resistance_bonuses[skill_type].append(bonus_text)
                            elif '护盾电磁伤害抗性' in bonus_text or '护盾热能伤害抗性' in bonus_text or '护盾动能伤害抗性' in bonus_text or '护盾爆炸伤害抗性' in bonus_text:
                                # 收集护盾抗性加成
                                if skill_type not in shield_resistance_bonuses:
                                    shield_resistance_bonuses[skill_type] = []
                                shield_resistance_bonuses[skill_type].append(bonus_text)
                            elif '电磁伤害' in bonus_text or '爆炸伤害' in bonus_text or '动能伤害' in bonus_text or '热能伤害' in bonus_text or 'EM damage' in bonus_text or 'Explosive damage' in bonus_text or 'Kinetic damage' in bonus_text or 'Thermal damage' in bonus_text:
                                # 收集导弹伤害加成
                                if skill_type not in missile_damage_bonuses:
                                    missile_damage_bonuses[skill_type] = []
                                missile_damage_bonuses[skill_type].append(bonus_text)
                            elif ('速度加成' in bonus_text or '失准范围加成' in bonus_text or '最佳射程加成' in bonus_text or '爆炸半径加成' in bonus_text or '飞行时间加成' in bonus_text or '跟踪速度加成' in bonus_text or '爆炸速度加成' in bonus_text or '导弹速度加成' in bonus_text) and '武器扰断器最佳射程加成' not in bonus_text and '武器扰断器最佳射程和失准范围惩罚' not in bonus_text:
                                # 收集武器扰断器相关加成（除了武器扰断器最佳射程加成和惩罚）
                                if skill_type not in weapon_disruption_bonuses:
                                    weapon_disruption_bonuses[skill_type] = []
                                weapon_disruption_bonuses[skill_type].append(bonus_text)
                            else:
                                if bonus_text not in skill_bonuses_dict[skill_type]:
                                    skill_bonuses_dict[skill_type].append(bonus_text)
                    else:
                        # 特有加成
                        for bonus_text in bonus_texts:
                            # 特殊处理：battlecruiserMETRange、battlecruiserMETRange2、bcLargeEnergyTurretCPUNeedBonus、bcLargeEnergyTurretCapacitorNeedBonus直接添加，不做任何修改
                            if 'battlecruiserMETRange' in effect_name or 'battlecruiserMETRange2' in effect_name or 'bcLargeEnergyTurretCPUNeedBonus' in effect_name or 'bcLargeEnergyTurretCapacitorNeedBonus' in effect_name:
                                if bonus_text not in unique_bonuses:
                                    unique_bonuses.append(bonus_text)
                                continue
                            
                            # 处理磨难级海军型的特有加成
                            # 检查效果名称中是否包含'Maller'或'Navy'或'Imperial'或'Crucifier'
                            if 'Maller' in effect_name or 'Navy' in effect_name or 'Imperial' in effect_name or 'Crucifier' in effect_name:
                                if '最佳射程' in bonus_text and '武器扰断器' not in bonus_text:
                                    # 替换为武器扰断器惩罚
                                    bonus_text = bonus_text.replace('最佳射程', '武器扰断器最佳射程和失准范围惩罚')
                                elif '效果失准范围加成' in bonus_text:
                                    # 替换为武器扰断器惩罚
                                    bonus_text = bonus_text.replace('效果失准范围加成', '武器扰断器最佳射程和失准范围惩罚')
                                elif 'CPU需求降低' in bonus_text and '索敌扰断器' not in bonus_text:
                                    # 替换为索敌扰断器CPU需求降低
                                    bonus_text = bonus_text.replace('CPU需求降低', '索敌扰断器启动消耗和CPU需求降低')
                                elif '启动消耗减少' in bonus_text and '索敌扰断器' not in bonus_text:
                                    # 替换为索敌扰断器启动消耗降低
                                    bonus_text = bonus_text.replace('启动消耗减少', '索敌扰断器启动消耗和CPU需求降低')
                            # 额外处理：直接检查bonus_text中的内容
                            elif '最佳射程' in bonus_text and '武器扰断器' not in bonus_text:
                                # 替换为武器扰断器惩罚
                                bonus_text = bonus_text.replace('最佳射程', '武器扰断器最佳射程和失准范围惩罚')
                            # 处理科洛斯级的特有加成
                            if 'Crusader' in effect_name or 'Interceptor' in effect_name:
                                # 处理索敌扰断器启动消耗和CPU需求降低
                                if '索敌扰断器启动消耗和CPU需求降低' in bonus_text:
                                    # 替换为推进抑制系统启动消耗减少
                                    bonus_text = bonus_text.replace('索敌扰断器启动消耗和CPU需求降低', '推进抑制系统启动消耗减少')
                                    # 确保数值为正数
                                    import re
                                    match = re.search(r'(-?\d+\.?\d*)%', bonus_text)
                                    if match:
                                        bonus_value = match.group(1)
                                        if bonus_value.startswith('-'):
                                            bonus_value = bonus_value[1:]
                                        bonus_text = bonus_text.replace(match.group(1), bonus_value)
                            # 处理特有加成中的索敌扰断器启动消耗和CPU需求降低
                            if '推进抑制系统启动消耗减少' not in bonus_text and ('索敌扰断器启动消耗和CPU需求降低' in bonus_text or 'CPU需求降低' in bonus_text or '启动消耗减少' in bonus_text):
                                # 检查是否是科洛斯级
                                if 'Crusader' in effect_name or 'Interceptor' in effect_name:
                                    # 替换为推进抑制系统启动消耗减少
                                    if '索敌扰断器启动消耗和CPU需求降低' in bonus_text:
                                        bonus_text = bonus_text.replace('索敌扰断器启动消耗和CPU需求降低', '推进抑制系统启动消耗减少')
                                    elif 'CPU需求降低' in bonus_text:
                                        bonus_text = bonus_text.replace('CPU需求降低', '推进抑制系统启动消耗减少')
                                    elif '启动消耗减少' in bonus_text:
                                        bonus_text = bonus_text.replace('启动消耗减少', '推进抑制系统启动消耗减少')
                                    # 确保数值为正数
                                    import re
                                    match = re.search(r'(-?\d+\.?\d*)%', bonus_text)
                                    if match:
                                        bonus_value = match.group(1)
                                        if bonus_value.startswith('-'):
                                            bonus_value = bonus_value[1:]
                                        bonus_text = bonus_text.replace(match.group(1), bonus_value)
                            if bonus_text not in unique_bonuses:
                                unique_bonuses.append(bonus_text)
        
        # 处理装甲抗性加成
        for skill_type, bonuses in armor_resistance_bonuses.items():
            if len(bonuses) == 4:
                # 四种抗性都存在，只保留一个通用的装甲抗性加成
                # 提取加成值
                bonus_value = None
                for bonus in bonuses:
                    import re
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 创建通用的装甲抗性加成文本
                    armor_bonus = f"{bonus_value}% 装甲抗性加成"
                    if armor_bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(armor_bonus)
            else:
                # 否则保留所有装甲抗性加成
                for bonus in bonuses:
                    if bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(bonus)
        
        # 处理护盾抗性加成
        for skill_type, bonuses in shield_resistance_bonuses.items():
            if len(bonuses) == 4:
                # 四种抗性都存在，只保留一个通用的护盾抗性加成
                # 提取加成值
                bonus_value = None
                for bonus in bonuses:
                    import re
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 创建通用的护盾抗性加成文本
                    shield_bonus = f"{bonus_value}% 护盾抗性加成"
                    if shield_bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(shield_bonus)
            else:
                # 否则保留所有护盾抗性加成
                for bonus in bonuses:
                    if bonus not in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].append(bonus)
        
        # 处理导弹伤害加成（四种伤害类型）
        for skill_type, bonuses in missile_damage_bonuses.items():
            if len(bonuses) >= 4:
                # 提取加成值
                bonus_value = None
                for bonus in bonuses:
                    import re
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 移除所有单独的伤害加成
                    for bonus in bonuses:
                        if bonus in skill_bonuses_dict[skill_type]:
                            skill_bonuses_dict[skill_type].remove(bonus)
                    # 添加合并后的导弹伤害加成
                    # 检查是否是火箭
                    if any('Rocket' in bonus or 'rocket' in bonus for bonus in bonuses):
                        # 检查是否是轻型导弹
                        if any('Light' in bonus or 'light' in bonus or 'missile' in bonus.lower() for bonus in bonuses):
                            skill_bonuses_dict[skill_type].append(f"{bonus_value}% 轻型导弹和火箭伤害加成")
                        else:
                            skill_bonuses_dict[skill_type].append(f"{bonus_value}% 火箭伤害加成")
                    else:
                        # 检查是否是轻型导弹
                        if any('Light' in bonus or 'light' in bonus or 'missile' in bonus.lower() for bonus in bonuses):
                            skill_bonuses_dict[skill_type].append(f"{bonus_value}% 轻型导弹伤害加成")
                        else:
                            skill_bonuses_dict[skill_type].append(f"{bonus_value}% 导弹伤害加成")

        # 处理掠能器和能量中和器最佳射程和失准范围加成
        for skill_type, bonuses in skill_bonuses_dict.items():
            # 检查是否有简单的"最佳射程"和"效果失准范围加成"
            simple_optimal_bonuses = [b for b in bonuses if b == '20% 最佳射程' or b == '10% 最佳射程']
            specific_optimal_bonuses = [b for b in bonuses if '掠能器和能量中和器最佳射程加成' in b]
            simple_falloff_bonuses = [b for b in bonuses if b == '20% 效果失准范围加成' or b == '10% 效果失准范围加成']
            specific_falloff_bonuses = [b for b in bonuses if '掠能器和能量中和器失准范围加成' in b]
            
            # 如果有简单的最佳射程加成和特定的最佳射程加成，移除简单的
            if simple_optimal_bonuses and specific_optimal_bonuses:
                for bonus in simple_optimal_bonuses:
                    if bonus in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].remove(bonus)
            
            # 如果有简单的失准范围加成和特定的失准范围加成，移除简单的
            if simple_falloff_bonuses and specific_falloff_bonuses:
                for bonus in simple_falloff_bonuses:
                    if bonus in skill_bonuses_dict[skill_type]:
                        skill_bonuses_dict[skill_type].remove(bonus)
        
        # 处理武器扰断器效果加成
        for skill_type, bonuses in weapon_disruption_bonuses.items():
            # 分离武器扰断器惩罚和其他效果
            penalty_bonuses = [b for b in bonuses if '武器扰断器最佳射程和失准范围惩罚' in b]
            other_bonuses = [b for b in bonuses if '武器扰断器最佳射程和失准范围惩罚' not in b]
            
            # 处理其他武器扰断器效果
            if other_bonuses:
                # 提取加成值
                bonus_value = None
                for bonus in other_bonuses:
                    import re
                    match = re.search(r'(\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 对于科洛斯级（截击舰），显示为小型能量炮台跟踪速度加成
                    # 对于咒灭级，不添加这个加成
                    if skill_type == '截击舰' and is_crusader:
                        weapon_bonus = f"{bonus_value}% 小型能量炮台跟踪速度加成"
                        if weapon_bonus not in skill_bonuses_dict[skill_type]:
                            skill_bonuses_dict[skill_type].append(weapon_bonus)
                            # 移除所有单独的武器扰断器相关加成
                            for bonus in other_bonuses:
                                if bonus in skill_bonuses_dict[skill_type]:
                                    skill_bonuses_dict[skill_type].remove(bonus)
                    elif skill_type == '截击舰' and is_malediction:
                        # 对于咒灭级，只移除所有单独的武器扰断器相关加成，不添加新的加成
                        for bonus in other_bonuses:
                            if bonus in skill_bonuses_dict[skill_type]:
                                skill_bonuses_dict[skill_type].remove(bonus)
                    else:
                        # 创建通用的武器扰断器效果加成文本
                        weapon_bonus = f"{bonus_value}% 武器扰断器效果加成"
                        if weapon_bonus not in skill_bonuses_dict[skill_type]:
                            skill_bonuses_dict[skill_type].append(weapon_bonus)
                            # 移除所有单独的武器扰断器相关加成
                            for bonus in other_bonuses:
                                if bonus in skill_bonuses_dict[skill_type]:
                                    skill_bonuses_dict[skill_type].remove(bonus)
            
            # 移除武器扰断器惩罚效果（只在特有加成中显示）
            for bonus in penalty_bonuses:
                if bonus in skill_bonuses_dict[skill_type]:
                    skill_bonuses_dict[skill_type].remove(bonus)
        
        # 处理武器扰断器最佳射程加成
        # 这里不需要特殊处理，因为它已经被单独添加到skill_bonuses_dict中了
        
        # 处理磨难级海军型的武器扰断器和索敌扰断器效果
        # 处理技能加成中的重复效果
        for skill_type, bonuses in skill_bonuses_dict.items():
            # 检查是否有武器扰断器最佳射程和失准范围惩罚
            td_penalty_bonuses = [b for b in bonuses if '武器扰断器最佳射程和失准范围惩罚' in b]
            if len(td_penalty_bonuses) > 0:
                # 移除所有武器扰断器最佳射程和失准范围惩罚
                skill_bonuses_dict[skill_type] = [b for b in skill_bonuses_dict[skill_type] if '武器扰断器最佳射程和失准范围惩罚' not in b]
            
            # 检查是否有索敌扰断器启动消耗和CPU需求降低
            td_cap_cpu_bonuses = [b for b in bonuses if '索敌扰断器启动消耗和CPU需求降低' in b]
            if len(td_cap_cpu_bonuses) > 0:
                # 移除所有索敌扰断器启动消耗和CPU需求降低
                skill_bonuses_dict[skill_type] = [b for b in skill_bonuses_dict[skill_type] if '索敌扰断器启动消耗和CPU需求降低' not in b]
        
        # 处理科洛斯级和咒灭级的特有加成
        # 检查是否有截击舰操作的技能加成
        if '截击舰' in skill_bonuses_dict:
            # 对于科洛斯级，添加小型能量炮台跟踪速度加成
            if is_crusader:
                # 检查是否已经有小型能量炮台跟踪速度加成
                has_tracking_bonus = any('小型能量炮台跟踪速度加成' in bonus for bonus in skill_bonuses_dict['截击舰'])
                if not has_tracking_bonus:
                    # 添加小型能量炮台跟踪速度加成
                    skill_bonuses_dict['截击舰'].append("7.50% 小型能量炮台跟踪速度加成")
            # 对于咒灭级，添加跃迁扰频器和跃迁扰断器最佳射程加成
            elif is_malediction:
                # 移除不需要的加成
                filtered_bonuses = []
                for bonus in skill_bonuses_dict['截击舰']:
                    # 保留信号半径加成（还没被处理）
                    if '信号半径加成' in bonus:
                        filtered_bonuses.append(bonus)
                # 添加跃迁扰频器和跃迁扰断器最佳射程加成
                filtered_bonuses.append("5% 跃迁扰频器和跃迁扰断器最佳射程加成")
                skill_bonuses_dict['截击舰'] = filtered_bonuses
                
                # 直接添加拦截失效装置相关加成到特有加成中
                unique_bonuses.append("80% 推进抑制系统启动消耗减少")
                unique_bonuses.append("80% 拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低")
                unique_bonuses.append("100% 拦截失效装置持续时间加成")
                # 添加跃迁速度和跃迁加速加成
                unique_bonuses.append("60% 跃迁速度和跃迁加速加成")
        
        # 处理信号半径加成
        for skill_type, bonuses in skill_bonuses_dict.items():
            for i, bonus in enumerate(bonuses):
                if '信号半径加成' in bonus and (skill_type == '截击舰' or 'Interceptor' in skill_type):
                    # 提取加成值
                    import re
                    match = re.search(r'(-?\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        # 确保数值为正数
                        if bonus_value.startswith('-'):
                            bonus_value = bonus_value[1:]
                        # 替换为微型跃迁推进器的信号半径惩罚降低
                        bonuses[i] = f"{bonus_value}% 微型跃迁推进器的信号半径惩罚降低"
        
        # 处理特有加成中的重复效果
        # 检查是否是强制者级，它的特有加成应该是小型能量炮台最佳射程加成
        if 'Coercer' in item_name:
            # 检查是否有最佳射程加成
            set_optimal_bonuses = [b for b in unique_bonuses if '最佳射程' in b]
            if len(set_optimal_bonuses) > 0:
                # 提取加成值
                bonus_value = None
                for bonus in set_optimal_bonuses:
                    import re
                    match = re.search(r'(-?\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 移除所有最佳射程加成
                    unique_bonuses = [b for b in unique_bonuses if '最佳射程' not in b]
                    # 添加合并后的小型能量炮台最佳射程加成
                    unique_bonuses.append(f"{bonus_value}% 小型能量炮台最佳射程加成")
        else:
            # 检查是否有武器扰断器最佳射程和失准范围惩罚（排除远程装甲维修器相关的、中型能量炮台相关的、能量中和器相关的）
            td_penalty_bonuses = [b for b in unique_bonuses if ('武器扰断器最佳射程和失准范围惩罚' in b or ('最佳射程' in b and '中型能量炮台' not in b and '掠能器和能量中和器' not in b) or ('效果失准范围加成' in b and '中型能量炮台' not in b and '掠能器和能量中和器' not in b)) and '远程装甲维修器' not in b and 'battlecruiserMETRange2' not in b]
            if len(td_penalty_bonuses) > 0:
                # 提取加成值
                bonus_value = None
                for bonus in td_penalty_bonuses:
                    import re
                    match = re.search(r'(-?\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 移除所有武器扰断器最佳射程和失准范围惩罚以及最佳射程（排除远程装甲维修器相关的、中型能量炮台相关的、能量中和器相关的）
                    unique_bonuses = [b for b in unique_bonuses if not (('武器扰断器最佳射程和失准范围惩罚' in b or ('最佳射程' in b and '中型能量炮台' not in b and '掠能器和能量中和器' not in b) or ('效果失准范围加成' in b and '中型能量炮台' not in b and '掠能器和能量中和器' not in b)) and '远程装甲维修器' not in b and 'battlecruiserMETRange2' not in b)]
                    # 添加合并后的武器扰断器最佳射程和失准范围惩罚
                    unique_bonuses.append(f"{bonus_value}% 武器扰断器最佳射程和失准范围惩罚")
        

        
        # 检查是否有索敌扰断器启动消耗和CPU需求降低或推进抑制系统启动消耗减少
        td_cap_cpu_bonuses = [b for b in unique_bonuses if ('索敌扰断器启动消耗和CPU需求降低' in b or '推进抑制系统启动消耗减少' in b or 'CPU需求降低' in b or '启动消耗减少' in b) and '大型能量炮台' not in b]
        if len(td_cap_cpu_bonuses) > 0:
            # 对于咒灭级，不进行特殊处理，保持我们已经添加的特有加成
            if not is_malediction:
                # 提取加成值
                bonus_value = None
                for bonus in td_cap_cpu_bonuses:
                    import re
                    match = re.search(r'(-?\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        break
                if bonus_value:
                    # 移除所有相关加成（排除大型能量炮台相关的）
                    unique_bonuses = [b for b in unique_bonuses if not (('索敌扰断器启动消耗和CPU需求降低' in b or '推进抑制系统启动消耗减少' in b or 'CPU需求降低' in b or '启动消耗减少' in b) and '大型能量炮台' not in b)]
                    if is_crusader or is_executioner:
                        # 添加合并后的推进抑制系统启动消耗减少，确保为正数
                        if bonus_value.startswith('-'):
                            bonus_value = bonus_value[1:]
                        unique_bonuses.append(f"{bonus_value}% 推进抑制系统启动消耗减少")
                    elif 'Magnate' in item_name:
                        # 富豪级和富豪级海军型的特殊处理
                        unique_bonuses.append(f"{bonus_value}% 扫描探针发射器CPU需求降低")
                    elif 'Deacon' in item_name:
                        # 执事级的特殊处理
                        unique_bonuses.append(f"{bonus_value}% 远程装甲维修器启动消耗减少")
                    else:
                        # 添加合并后的索敌扰断器启动消耗和CPU需求降低，确保为负数
                        if not bonus_value.startswith('-'):
                            bonus_value = '-' + bonus_value
                        unique_bonuses.append(f"{bonus_value}% 索敌扰断器启动消耗和CPU需求降低")
        
        # 处理科洛斯级和咒灭级的特有加成
        # 检查是否有截击舰操作的技能加成
        if '截击舰' in skill_bonuses_dict:
            # 对于科洛斯级，添加小型能量炮台跟踪速度加成
            if is_crusader:
                # 检查是否已经有小型能量炮台跟踪速度加成
                has_tracking_bonus = any('小型能量炮台跟踪速度加成' in bonus for bonus in skill_bonuses_dict['截击舰'])
                if not has_tracking_bonus:
                    # 添加小型能量炮台跟踪速度加成
                    skill_bonuses_dict['截击舰'].append("7.50% 小型能量炮台跟踪速度加成")
            # 对于咒灭级，添加跃迁扰频器和跃迁扰断器最佳射程加成
            elif is_malediction:
                # 移除不需要的加成
                filtered_bonuses = []
                for bonus in skill_bonuses_dict['截击舰']:
                    # 保留信号半径加成（还没被处理）
                    if '信号半径加成' in bonus:
                        filtered_bonuses.append(bonus)
                # 添加跃迁扰频器和跃迁扰断器最佳射程加成
                filtered_bonuses.append("5% 跃迁扰频器和跃迁扰断器最佳射程加成")
                skill_bonuses_dict['截击舰'] = filtered_bonuses
                
                # 直接添加拦截失效装置相关加成到特有加成中
                unique_bonuses.append("80% 推进抑制系统启动消耗减少")
                unique_bonuses.append("80% 拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低")
                unique_bonuses.append("100% 拦截失效装置持续时间加成")
                # 添加跃迁速度和跃迁加速加成
                unique_bonuses.append("60% 跃迁速度和跃迁加速加成")

        # 处理信号半径加成
        for skill_type, bonuses in skill_bonuses_dict.items():
            for i, bonus in enumerate(bonuses):
                if '信号半径加成' in bonus and (skill_type == '截击舰' or 'Interceptor' in skill_type):
                    # 提取加成值
                    import re
                    match = re.search(r'(-?\d+\.?\d*)%', bonus)
                    if match:
                        bonus_value = match.group(1)
                        # 确保数值为正数
                        if bonus_value.startswith('-'):
                            bonus_value = bonus_value[1:]
                        # 替换为微型跃迁推进器的信号半径惩罚降低
                        bonuses[i] = f"{bonus_value}% 微型跃迁推进器的信号半径惩罚降低"

        # 处理战列舰特有加成
        battleship_role_bonuses = [b for b in unique_bonuses if 'Shield Hitpoint Bonus' in b or '装甲值加成' in b or 'Structure Hitpoint Bonus' in b]
        if len(battleship_role_bonuses) > 0:
            # 移除原来的战列舰特有加成
            unique_bonuses = [b for b in unique_bonuses if 'Shield Hitpoint Bonus' not in b and '装甲值加成' not in b and 'Structure Hitpoint Bonus' not in b]
            # 添加合并后的战列舰特有加成
            unique_bonuses.append("100% 护盾扩展装置护盾值加成")
            unique_bonuses.append("50% 装甲附甲板装甲值加成")
            unique_bonuses.append("5% 强化舱隔壁结构值加成")
        
        return skill_bonuses_dict, unique_bonuses
    
    async def _process_modifiers(self, modifiers, attr_dict, effect_name):
        """处理modifiers，生成加成文本"""
        bonus_texts = []
        
        for mod in modifiers:
            bonus_value = None
            bonus_attribute = None
            modified_attr_id = mod.get('modified_attribute_id')
            
            # 获取加成值
            modifying_attr_id = mod.get('modifying_attribute_id')
            if modifying_attr_id and modifying_attr_id in attr_dict:
                bonus_value = attr_dict[modifying_attr_id]
            
            # 获取加成属性
            if modified_attr_id:
                attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
                if attr_info:
                    bonus_attribute = attr_info.get('display_name', attr_info.get('name', ''))
            
            # 处理加成
            if bonus_value and bonus_attribute:
                bonus_text = await self._process_bonus(bonus_value, bonus_attribute, effect_name, modified_attr_id)
                if bonus_text:
                    bonus_texts.append(bonus_text)
            elif bonus_value and modified_attr_id:
                # 没有中文显示名称，尝试使用英文名称
                bonus_text = await self._process_bonus_without_display_name(bonus_value, modified_attr_id, bonus_attribute)
                if bonus_text:
                    bonus_texts.append(bonus_text)
        
        return bonus_texts
    
    async def _process_bonus(self, bonus_value, bonus_attribute, effect_name, modified_attr_id):
        """处理单个加成"""
        # 尝试从加成处理字典中获取处理函数
        bonus_text = None
        
        # 获取英文属性名称
        attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
        attr_name = attr_info.get('name', '') if attr_info else ''
        
        # 调试日志
        logger.info(f"处理加成: bonus_value={bonus_value}, bonus_attribute={bonus_attribute}, effect_name={effect_name}, modified_attr_id={modified_attr_id}, attr_name={attr_name}")
        
        # 优先检查英文属性名称（完全匹配）
        if attr_name in self.bonus_handlers:
            try:
                bonus_text = self.bonus_handlers[attr_name](bonus_value, effect_name)
                logger.info(f"使用英文属性名称完全匹配: {attr_name} -> {bonus_text}")
            except Exception as e:
                logger.error(f"处理加成时出错: {e}")
        
        # 然后检查英文属性名称（包含匹配）
        if bonus_text is None:
            for key, handler in self.bonus_handlers.items():
                if key in attr_name:
                    try:
                        bonus_text = handler(bonus_value, effect_name)
                        logger.info(f"使用英文属性名称包含匹配: {key} in {attr_name} -> {bonus_text}")
                    except Exception as e:
                        logger.error(f"处理加成时出错: {e}")
                    break
        
        # 然后检查效果名称（完全匹配）
        if bonus_text is None:
            if effect_name in self.bonus_handlers:
                try:
                    bonus_text = self.bonus_handlers[effect_name](bonus_value, effect_name)
                    logger.info(f"使用效果名称完全匹配: {effect_name} -> {bonus_text}")
                except Exception as e:
                    logger.error(f"处理加成时出错: {e}")
        
        # 然后检查效果名称（包含匹配）
        if bonus_text is None:
            # 特殊处理：如果效果名称包含'Gunship'和'Armor'，则处理为装甲抗性加成
            if 'Gunship' in effect_name and ('Armor' in effect_name or 'Resistance' in effect_name):
                # 获取属性信息以确定具体的抗性类型
                if attr_name in ['armorEmDamageResonance', 'armorThermalDamageResonance', 'armorKineticDamageResonance', 'armorExplosiveDamageResonance']:
                    bonus_text = f"{self._format_bonus_value(abs(bonus_value))}% 装甲{attr_name.split('armor')[1].replace('DamageResonance', '')}伤害抗性"
                    logger.info(f"effect_name contains: Gunship and Armor, bonus_text: {bonus_text}")
            else:
                for key, handler in self.bonus_handlers.items():
                    if key in effect_name:
                        try:
                            bonus_text = handler(bonus_value, effect_name)
                            logger.info(f"使用效果名称包含匹配: {key} in {effect_name} -> {bonus_text}")
                        except Exception as e:
                            logger.error(f"处理加成时出错: {e}")
                        break
        
        # 最后检查中文属性名称（包含匹配）
        if bonus_text is None:
            for key, handler in self.bonus_handlers.items():
                if key in bonus_attribute:
                    try:
                        bonus_text = handler(bonus_value, effect_name)
                        logger.info(f"使用中文属性名称包含匹配: {key} in {bonus_attribute} -> {bonus_text}")
                    except Exception as e:
                        logger.error(f"处理加成时出错: {e}")
                    break
        
        # 如果没有找到处理函数，使用通用格式
        if bonus_text is None:
            # 处理伤害量调整
            if 'damageMultiplier' in attr_name:
                bonus_text = self._handle_damage_bonus(bonus_value, effect_name, bonus_attribute)
                logger.info(f"使用伤害量调整处理: {bonus_text}")
            # 处理打捞装置运转周期
            elif 'SalvageCycle' in attr_name:
                bonus_text = f"{self._format_bonus_value(abs(bonus_value))}% 打捞装置运转周期降低"
                logger.info(f"使用打捞装置运转周期处理: {bonus_text}")
            # 处理扫描强度基数
            elif 'scanStrengthBonus' in attr_name:
                bonus_text = f"{self._format_bonus_value(bonus_value)}% 核心和作战扫描探针强度加成"
                logger.info(f"使用扫描强度基数处理: {bonus_text}")
            elif bonus_value < 0:
                bonus_text = f"{self._format_bonus_value(abs(bonus_value))}% {bonus_attribute}"
                logger.info(f"使用通用负加成处理: {bonus_text}")
            else:
                bonus_text = f"{self._format_bonus_value(bonus_value)}% {bonus_attribute}"
                logger.info(f"使用通用正加成处理: {bonus_text}")
        
        logger.info(f"最终加成文本: {bonus_text}")
        return bonus_text
    
    async def _process_bonus_without_display_name(self, bonus_value, modified_attr_id, bonus_attribute):
        """处理没有显示名称的加成"""
        attr_info = await self.esi_request(f"/v1/dogma/attributes/{modified_attr_id}/")
        if attr_info:
            attr_name = attr_info.get('name', '未知属性')
            if 'entosisCPUAdd' in attr_name:
                # 跳过entosisCPUAdd，这是负面效果
                return None
            if 'warpCapacitorNeed' in attr_name or modified_attr_id == 153:
                return f"{self._format_bonus_value(abs(bonus_value))}%跃迁引擎电容需求降低"
            elif 'scanProbeDeviation' in attr_name:
                return f"{self._format_bonus_value(abs(bonus_value))}%核心和作战扫描探针扫描偏差减少"
            elif 'surveyProbeExplosionDelay' in attr_name:
                return f"{self._format_bonus_value(abs(bonus_value))}%测量探针扫描时间减少"
            elif 'warpFactor' in attr_name or modified_attr_id == 21:
                return f"{self._format_bonus_value(abs(bonus_value))}%隐形装置的CPU需求降低"
            elif 'warpSpeedMultiplier' in attr_name:
                return f"{self._format_bonus_value(bonus_value)}%跃迁速度和跃迁加速加成"
            elif bonus_value < 0:
                return f"{self._format_bonus_value(abs(bonus_value))}%{attr_name}"
            else:
                return f"{self._format_bonus_value(bonus_value)}%{attr_name}"
        return None
    
    def _identify_skill_type(self, effect_name):
        """识别技能类型"""
        # 1. 首先检查高优先级的键（避免误匹配）
        high_priority_keys = ['AC', 'ABC', 'ABS', 'AB', 'AmarrCruiser', 'AmarrBattlecruiser', 'AmarrBattleship']
        for key in high_priority_keys:
            if key in effect_name:
                return self.skill_type_map[key]
        
        # 2. 然后检查技能类型映射（最直接和准确）
        # 按键长度降序排序，优先匹配更长的键
        sorted_keys = sorted(self.skill_type_map.keys(), key=len, reverse=True)
        for key in sorted_keys:
            if key in high_priority_keys:
                continue
            if key in effect_name:
                return self.skill_type_map[key]
        
        # 2. 如果没有识别到，检查种族和吨位（基础分类）
        race = None
        race_types = {'Amarr': '艾玛', 'Gallente': '盖伦特', 'Minmatar': '米玛塔尔', 'Caldari': '加达里'}
        for race_key, race_name in race_types.items():
            if race_key in effect_name:
                race = race_name
                break
        
        # 检查吨位
        if race:
            if 'Frigate' in effect_name:
                return f'{race}护卫舰'
            elif 'Destroyer' in effect_name:
                return f'{race}驱逐舰'
            elif 'Cruiser' in effect_name:
                return f'{race}巡洋舰'
            elif 'Battlecruiser' in effect_name:
                return f'{race}战列巡洋舰'
            elif 'Battleship' in effect_name:
                return f'{race}战列舰'
        
        # 3. 检查特殊舰船类型（二级分类）
        if 'Assault' in effect_name or 'assault' in effect_name:
            return '突击护卫舰'
        elif 'Interceptor' in effect_name or 'interceptor' in effect_name:
            return '截击舰'
        elif 'CovertOps' in effect_name or 'covert' in effect_name:
            return '隐形特勤舰'
        elif 'ElectronicAttackShip' in effect_name or 'EAF' in effect_name:
            return '电子攻击舰'
        
        return None
    
    def _build_result(self, item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_cn):
        """构建结果文本"""
        # 如果有中文名称，使用中文名称；否则使用英文名称
        if item_name_cn:
            display_name = item_name_cn
        else:
            display_name = item_info.get('name', '未知')
        
        result = f"{display_name}\n\n"
        
        # 收集所有加成，计算最长数值长度
        all_bonuses = []
        if '截击舰' in skill_bonuses_dict:
            # 移除武器扰断器效果加成
            filtered_bonuses = [bonus for bonus in skill_bonuses_dict['截击舰'] if '武器扰断器效果加成' not in bonus]
            all_bonuses.extend(filtered_bonuses)
        if '艾玛护卫舰' in skill_bonuses_dict:
            all_bonuses.extend(skill_bonuses_dict['艾玛护卫舰'])
        for skill_type, bonuses in skill_bonuses_dict.items():
            if skill_type not in ['截击舰', '艾玛护卫舰']:
                all_bonuses.extend(bonuses)
        all_bonuses.extend(unique_bonuses)
        
        # 计算最长数值长度（包括%符号）
        max_value_length = 0
        for bonus in all_bonuses:
            percent_pos = bonus.find('%')
            if percent_pos != -1:
                value_length = percent_pos + 1  # 包括%符号
                if value_length > max_value_length:
                    max_value_length = value_length
        
        # 按照指定顺序输出技能加成
        # 1. 截击舰操作
        if '截击舰' in skill_bonuses_dict and skill_bonuses_dict['截击舰']:
            # 移除武器扰断器效果加成
            filtered_bonuses = [bonus for bonus in skill_bonuses_dict['截击舰'] if '武器扰断器效果加成' not in bonus]
            result += self._format_skill_bonuses('截击舰', filtered_bonuses, max_value_length)
        
        # 2. 艾玛护卫舰操作
        if '艾玛护卫舰' in skill_bonuses_dict and skill_bonuses_dict['艾玛护卫舰']:
            # 根据jiacheng.txt调整艾玛护卫舰操作技能加成的顺序
            amarr_bonuses = skill_bonuses_dict['艾玛护卫舰']
            ordered_amarr_bonuses = []
            
            # 优先级顺序：维修量加成 &gt; 启动消耗减少 &gt; 武器扰断器效果 &gt; 武器扰断器最佳射程 &gt; 小型能量炮台最佳射程 &gt; 核心和作战扫描探针强度 &gt; 小型能量炮台伤害 &gt; 其他
            priority_order = [
                '远程装甲维修器维修量加成',
                '远程装甲维修器启动消耗减少',
                '小型能量炮台启动消耗减少',
                '小型能量炮台最佳射程加成',
                '核心和作战扫描探针强度加成',
                '武器扰断器效果加成',
                '武器扰断器最佳射程加成',
                '小型能量炮台伤害加成',
                '小型能量炮台跟踪速度加成',
                '打捞装置运转周期降低',
                '装甲抗性加成',
                '火箭发射器射速加成',
                '轻型导弹和火箭伤害加成',
                '火箭和轻型导弹发射器射速加成',
                '鱼雷飞行时间加成',
                '鱼雷飞行速度加成',
                '掠能器和能量中和器吸取量加成',
                '远程装甲维修器运转周期和启动消耗减少',
                '装甲值加成'
            ]
            
            # 按照优先级顺序添加加成
            for priority in priority_order:
                for bonus in amarr_bonuses:
                    if priority in bonus and bonus not in ordered_amarr_bonuses:
                        ordered_amarr_bonuses.append(bonus)
            
            # 添加剩余的加成
            for bonus in amarr_bonuses:
                if bonus not in ordered_amarr_bonuses:
                    ordered_amarr_bonuses.append(bonus)
            
            result += self._format_skill_bonuses('艾玛护卫舰', ordered_amarr_bonuses, max_value_length)
        
        # 3. 其他技能操作
        for skill_type, bonuses in skill_bonuses_dict.items():
            if bonuses and skill_type not in ['截击舰', '艾玛护卫舰']:
                result += self._format_skill_bonuses(skill_type, bonuses, max_value_length)
        
        # 4. 特有加成
        if unique_bonuses:
            result += "特有加成:\n"
            
            # 检查是否是咒灭级
            is_malediction = 'Malediction' in item_info.get('name', '')
            
            # 调整特有加成的顺序
            ordered_bonuses = []
            
            if is_malediction:
                # 咒灭级特有加成顺序
                # 1. 推进抑制系统启动消耗减少
                # 2. 拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低
                # 3. 拦截失效装置持续时间加成
                # 4. 跃迁速度和跃迁加速加成
                
                # 查找并添加推进抑制系统启动消耗减少
                for bonus in unique_bonuses:
                    if '推进抑制系统启动消耗减少' in bonus:
                        ordered_bonuses.append(bonus)
                        break
                
                # 查找并添加拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低
                for bonus in unique_bonuses:
                    if '拦截失效装置重启延迟、最大锁定距离惩罚和扫描分辨率惩罚降低' in bonus:
                        ordered_bonuses.append(bonus)
                        break
                
                # 查找并添加拦截失效装置持续时间加成
                for bonus in unique_bonuses:
                    if '拦截失效装置持续时间加成' in bonus:
                        ordered_bonuses.append(bonus)
                        break
                
                # 查找并添加跃迁速度和跃迁加速加成
                for bonus in unique_bonuses:
                    if '跃迁速度和跃迁加速加成' in bonus:
                        ordered_bonuses.append(bonus)
                        break
            else:
                # 其他舰船特有加成顺序
                # 1. 推进抑制系统启动消耗减少
                # 2. 加力燃烧器和微型跃迁推进器过载效果加成
                # 3. 跃迁速度和跃迁加速加成
                
                # 查找并添加推进抑制系统启动消耗减少
                for bonus in unique_bonuses:
                    if '推进抑制系统启动消耗减少' in bonus:
                        ordered_bonuses.append(bonus)
                        break
                
                # 查找并添加加力燃烧器和微型跃迁推进器过载效果加成
                for bonus in unique_bonuses:
                    if '加力燃烧器和微型跃迁推进器过载效果加成' in bonus or '超载速度加成' in bonus:
                        # 替换'超载速度加成'为'加力燃烧器和微型跃迁推进器过载效果加成'
                        if '超载速度加成' in bonus:
                            bonus = bonus.replace('超载速度加成', '加力燃烧器和微型跃迁推进器过载效果加成')
                        ordered_bonuses.append(bonus)
                        break
                
                # 查找并添加跃迁速度和跃迁加速加成
                for bonus in unique_bonuses:
                    if '跃迁速度和跃迁加速加成' in bonus:
                        ordered_bonuses.append(bonus)
                        break
            
            # 添加其他特有加成（如果有的话）
            for bonus in unique_bonuses:
                # 跳过'超载速度加成'，因为它已经被替换为'加力燃烧器和微型跃迁推进器过载效果加成'
                if '超载速度加成' in bonus:
                    continue
                # 跳过'作用时间/单次运转时间'，因为它已经被替换为'拦截失效装置持续时间加成'
                if '作用时间/单次运转时间' in bonus:
                    continue
                if bonus not in ordered_bonuses:
                    ordered_bonuses.append(bonus)
            
            for bonus in ordered_bonuses:
                # 计算缩进
                percent_pos = bonus.find('%')
                if percent_pos != -1:
                    value_length = percent_pos + 1
                    # 计算数值部分的缩进，使%符号对齐
                    num_indent = ' ' * (max_value_length - value_length)
                    # 基础缩进 + 数值部分缩进 + 数值 + % + 空格 + 文字
                    result += f"  {num_indent}{bonus[:percent_pos + 1]} {bonus[percent_pos + 1:].strip()}\n"
                else:
                    # 不带%的加成，需要与其他加成的文字部分对齐
                    # 计算总缩进：2个空格（基础缩进） + max_len（数值部分长度） + 1个空格（%后空格）
                    total_indent = 2 + max_value_length + 1
                    result += f"{' ' * total_indent}{bonus}\n"
            result += "\n"
        
        # 槽位信息
        low_slots = int(attr_dict.get(12, 0))
        mid_slots = int(attr_dict.get(13, 0))
        high_slots = int(attr_dict.get(14, 0))
        result += f"低能量槽: {low_slots}\n"
        result += f"中能量槽: {mid_slots}\n"
        result += f"高能量槽: {high_slots}\n"
        
        # 改装件槽
        rig_slots = int(attr_dict.get(15, 0))
        if rig_slots > 0:
            result += f"改装件槽: {rig_slots}\n"
        
        # 无人机带宽
        drone_bandwidth = int(attr_dict.get(1271, 0))
        if drone_bandwidth > 0:
            result += f"无人机带宽: {drone_bandwidth} Mbit/s\n"
        
        # CPU和能量栅格
        cpu = int(attr_dict.get(48, 0))
        result += f"CPU输出: {cpu} tf\n"
        powergrid = int(attr_dict.get(49, 0))
        result += f"能量栅格: {powergrid} MW\n"
        
        # 校准值
        calibration = int(attr_dict.get(162, 0))
        if calibration > 0:
            result += f"校准值: {calibration}\n"
        
        # 改装件尺寸
        rig_size = attr_dict.get(1547, '未知')
        rig_size_text = {1: '小型', 2: '中型', 3: '大型', 4: '超大型'}.get(int(rig_size) if rig_size and rig_size != '未知' else rig_size, rig_size)
        result += f"改装件尺寸: {rig_size_text}\n\n"
        
        # 护盾信息
        shield_hp = int(attr_dict.get(263, 0))
        result += f"护盾容量: {shield_hp} HP\n"
        # 抗性计算：ESI返回的是damage_resonance，需要转换为抗性
        # 顺序：电磁、热能、动能、爆炸
        shield_em = (1 - attr_dict.get(271, 1)) * 100
        shield_therm = (1 - attr_dict.get(274, 1)) * 100
        shield_kin = (1 - attr_dict.get(273, 1)) * 100
        shield_exp = (1 - attr_dict.get(272, 1)) * 100
        result += f"护盾电磁抗性: {int(shield_em)}%\n"
        result += f"护盾热能抗性: {int(shield_therm)}%\n"
        result += f"护盾动能抗性: {int(shield_kin)}%\n"
        result += f"护盾爆炸抗性: {int(shield_exp)}%\n\n"
        
        # 装甲信息
        armor_hp = int(attr_dict.get(265, 0))
        result += f"装甲值: {armor_hp} HP\n"
        # 顺序：电磁、热能、动能、爆炸
        armor_em = (1 - attr_dict.get(267, 1)) * 100
        armor_therm = (1 - attr_dict.get(270, 1)) * 100
        armor_kin = (1 - attr_dict.get(269, 1)) * 100
        armor_exp = (1 - attr_dict.get(268, 1)) * 100
        result += f"装甲电磁抗性: {int(armor_em)}%\n"
        result += f"装甲热能抗性: {int(armor_therm)}%\n"
        result += f"装甲动能抗性: {int(armor_kin)}%\n"
        result += f"装甲爆炸抗性: {int(armor_exp)}%\n\n"
        
        # 结构信息
        structure_hp = int(attr_dict.get(9, 0))
        result += f"结构值: {structure_hp} HP\n\n"
        
        # 速度信息
        max_velocity = int(attr_dict.get(37, 0))
        result += f"最大速度: {max_velocity} m/s\n"
        warp_speed = attr_dict.get(600, 0)
        if warp_speed > 0:
            result += f"跃迁速度: {warp_speed} AU/s\n"
        inertia = attr_dict.get(70, 0)
        result += f"惯性调整: {inertia} x\n"
        mass = int(attr_dict.get(55, 0))
        result += f"质量: {mass:,} kg\n\n"
        
        # 锁定信息
        scan_res = int(attr_dict.get(38, 0))
        result += f"扫描分辨率: {scan_res} mm\n"
        max_range = int(attr_dict.get(76, 0) / 1000)  # 转换为公里
        result += f"锁定范围: {max_range} km\n"
        max_locked = int(attr_dict.get(192, 0))
        result += f"最大锁定目标数: {max_locked}\n\n"
        
        # 体积信息
        volume = item_info.get('volume', 0)
        result += f"体积: {volume} m³\n"
        # 根据改装件尺寸推断包装体积
        packaged_volume_map = {1: 2500, 2: 10000, 3: 50000, 4: 1000000}
        rig_size = attr_dict.get(1547, 0)
        if rig_size in packaged_volume_map:
            result += f"包装体积: {packaged_volume_map[rig_size]:,} m³\n"
        
        # 货舱容量
        capacity = int(item_info.get('capacity', 0))
        result += f"货舱容量: {capacity} m³\n"
        
        # 无人机容量
        drone_capacity = int(attr_dict.get(36, 0))
        if drone_capacity > 0:
            result += f"无人机容量: {drone_capacity} m³\n"
        
        # 舰船维护舱容量
        ship_maintenance = int(attr_dict.get(908, 0))
        if ship_maintenance > 0:
            result += f"舰船维护舱容量: {ship_maintenance:,} m³\n"
        
        # 舰队机库容量
        fleet_hangar = int(attr_dict.get(912, 0))
        if fleet_hangar > 0:
            result += f"舰队机库容量: {fleet_hangar:,} m³\n"
        
        return result
    
    def _format_skill_bonuses(self, skill_type, bonuses, max_value_length):
        """格式化技能加成"""
        # 去重相同的加成
        unique_bonuses = []
        seen_bonuses = set()
        for bonus in bonuses:
            # 提取加成的核心部分（去除数值）
            percent_pos = bonus.find('%')
            if percent_pos != -1:
                bonus_core = bonus[percent_pos+1:].strip()
            else:
                bonus_core = bonus.strip()
            # 检查是否已经添加过类似的加成
            if bonus_core not in seen_bonuses:
                seen_bonuses.add(bonus_core)
                unique_bonuses.append(bonus)
        
        if not unique_bonuses:
            return ""
        
        result = f"{skill_type}操作每升一级:\n"
        for bonus in unique_bonuses:
            formatted_bonus = self._format_bonus_line(bonus)
            # 计算缩进
            percent_pos = formatted_bonus.find('%')
            if percent_pos != -1:
                value_length = percent_pos + 1
                # 计算数值部分的缩进，使%符号对齐
                num_indent = ' ' * (max_value_length - value_length)
                # 基础缩进 + 数值部分缩进 + 数值 + % + 空格 + 文字
                result += f"  {num_indent}{formatted_bonus[:percent_pos + 1]} {formatted_bonus[percent_pos + 2:]}\n"
            else:
                # 不带%的加成，需要与其他加成的文字部分对齐
                # 计算总缩进：2个空格（基础缩进） + max_len（数值部分长度） + 1个空格（%后空格）
                total_indent = 2 + max_value_length + 1
                result += f"{' ' * total_indent}{bonus}\n"
        result += "\n"
        
        return result

    async def _get_item_info(self, item_id, event, item_name_cn=''):
        """获取物品信息的内部方法"""
        item_info = await self.esi_request(f"/v3/universe/types/{item_id}/")
        if item_info:
            # 提取属性
            attr_dict = self._extract_attributes(item_info)
            
            # 处理技能加成和特有加成
            dogma_effects = item_info.get('dogma_effects', [])
            skill_bonuses_dict, unique_bonuses = await self._process_bonuses(dogma_effects, attr_dict, self.session, item_info.get('name', ''))
            
            # 构建结果
            result = self._build_result(item_info, skill_bonuses_dict, unique_bonuses, attr_dict, item_name_cn)
            
            yield event.plain_result(result)
        else:
            yield event.plain_result(f"未找到物品ID {item_id} 的信息")

    async def esi_request(self, endpoint, method="GET", data=None):
        """发送ESI请求"""
        base_url = "https://ali-esi.evepc.163.com"
        url = f"{base_url}{endpoint}"
        
        try:
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    if method == "GET":
                        async with session.get(url) as response:
                            if response.status == 200:
                                return await response.json()
                            else:
                                logger.error(f"ESI请求失败: {response.status} - {url}")
                                return None
                    elif method == "POST":
                        async with session.post(url, json=data) as response:
                            if response.status == 200:
                                return await response.json()
                            else:
                                logger.error(f"ESI请求失败: {response.status} - {url}")
                                return None
            else:
                # 使用已初始化的session
                if method == "GET":
                    async with self.session.get(url) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            logger.error(f"ESI请求失败: {response.status} - {url}")
                            return None
                elif method == "POST":
                    async with self.session.post(url, json=data) as response:
                        if response.status == 200:
                            return await response.json()
                        else:
                            logger.error(f"ESI请求失败: {response.status} - {url}")
                            return None
        except Exception as e:
            logger.error(f"ESI请求异常: {e} - {url}")
            return None

    async def search_item_by_name(self, name):
        """使用市场中心API搜索物品，如果失败则使用ESI搜索API作为备选
        注意：市场中心API需要使用POST请求，不能使用GET请求
        """
        # 先尝试使用市场中心API
        try:
            url = "https://www.ceve-market.org/api/searchname"
            data = {"name": name}
            
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=data) as response:
                        if response.status == 200:
                            result = await response.json()
                            logger.info(f"市场中心搜索结果: {len(result)}个物品")
                            return result
                        else:
                            logger.error(f"市场中心搜索失败: {response.status}，尝试使用ESI搜索API")
                            # 市场中心API失败，尝试使用ESI搜索API
                            return await self._search_item_by_name_esi(name)
            else:
                # 使用已初始化的session
                async with self.session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"市场中心搜索结果: {len(result)}个物品")
                        return result
                    else:
                        logger.error(f"市场中心搜索失败: {response.status}，尝试使用ESI搜索API")
                        # 市场中心API失败，尝试使用ESI搜索API
                        return await self._search_item_by_name_esi(name)
        except Exception as e:
            logger.error(f"市场中心搜索异常: {e}，尝试使用ESI搜索API")
            # 市场中心API异常，尝试使用ESI搜索API
            return await self._search_item_by_name_esi(name)
    
    async def _search_item_by_name_esi(self, name):
        """使用ESI搜索API搜索物品"""
        try:
            base_url = "https://ali-esi.evepc.163.com/latest"
            url = f"{base_url}/universe/ids/"
            
            # 使用类初始化时创建的session
            if not self.session:
                # 如果session未初始化，创建一个临时session
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=[name]) as response:
                        if response.status == 200:
                            result = await response.json()
                            inventory_types = result.get('inventory_types', [])
                            # 转换为与市场中心API相同的格式
                            items = []
                            for item in inventory_types:
                                items.append({
                                    'typeid': item.get('id'),
                                    'typename': item.get('name')
                                })
                            logger.info(f"ESI搜索API结果: {len(items)}个物品")
                            return items
                        else:
                            logger.error(f"ESI搜索API失败: {response.status}")
                            return []
            else:
                # 使用已初始化的session
                async with self.session.post(url, json=[name]) as response:
                    if response.status == 200:
                        result = await response.json()
                        inventory_types = result.get('inventory_types', [])
                        # 转换为与市场中心API相同的格式
                        items = []
                        for item in inventory_types:
                            items.append({
                                'typeid': item.get('id'),
                                'typename': item.get('name')
                            })
                        logger.info(f"ESI搜索API结果: {len(items)}个物品")
                        return items
                    else:
                        logger.error(f"ESI搜索API失败: {response.status}")
                        return []
        except Exception as e:
            logger.error(f"ESI搜索API异常: {e}")
            return []

    def _is_skin(self, item_name):
        """判断物品是否为涂装（SKIN）"""
        skin_keywords = ['涂装', 'Skin', 'SKIN', 'skin']
        return any(keyword in item_name for keyword in skin_keywords)
    
    def _is_blueprint(self, item_name):
        """判断物品是否为蓝图"""
        blueprint_keywords = ['蓝图', 'Blueprint', 'BLUEPRINT', 'blueprint']
        return any(keyword in item_name for keyword in blueprint_keywords)
    
    def _format_bonus_value(self, value):
        """格式化加成数值，当有小数时保留两位小数，没有小数时保留整数"""
        if isinstance(value, (int, float)):
            if value.is_integer():
                return f"{int(value)}"
            else:
                return f"{value:.2f}"
        return str(value)
    
    def _format_bonus_line(self, bonus_text):
        """格式化加成行，确保数值与文字之间有一个空格，并且数值后的文字第一个字符对齐"""
        # 查找第一个%符号的位置
        percent_pos = bonus_text.find('%')
        if percent_pos != -1:
            # 在%后面添加一个空格
            formatted_text = bonus_text[:percent_pos + 1] + ' ' + bonus_text[percent_pos + 1:]
            return formatted_text
        return bonus_text
    
    def _handle_damage_bonus(self, bonus_value, effect_name, bonus_attribute):
        """处理伤害加成"""
        # 检查是否是无人机伤害加成
        if 'Drone' in effect_name or 'drone' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 无人机伤害加成"
        
        # 检查是否是导弹伤害加成
        if 'missile' in effect_name.lower() or 'rocket' in effect_name.lower():
            # 检查是否有四种伤害类型
            if 'emDamageBonus' in effect_name or 'explosiveDamageBonus' in effect_name or 'kineticDamageBonus' in effect_name or 'thermalDamageBonus' in effect_name:
                # 检查效果名称中是否包含具体的导弹类型
                if 'rocket' in effect_name.lower():
                    return f"{self._format_bonus_value(bonus_value)}% 火箭伤害加成"
                elif 'light' in effect_name.lower() or 'lightMissile' in effect_name.lower():
                    return f"{self._format_bonus_value(bonus_value)}% 轻型导弹伤害加成"
                else:
                    return f"{self._format_bonus_value(bonus_value)}% 导弹伤害加成"
        
        # 检查是否是大型能量炮台伤害加成
        if 'LEDmg' in effect_name or 'LargeEnergyTurretDamage' in effect_name or 'LargeEnergy' in effect_name or 'ABS' in effect_name or 'AmarrBattleship' in effect_name or ('ABC' in effect_name and 'Large' in effect_name):
            return f"{self._format_bonus_value(bonus_value)}% 大型能量炮台伤害加成"
        # 检查是否是中型能量炮台伤害加成
        elif 'MEDmg' in effect_name or 'MediumEnergyTurretDamage' in effect_name or 'MediumEnergy' in effect_name or 'AC' in effect_name or 'AmarrCruiser' in effect_name or ('ABC' in effect_name and not 'Large' in effect_name and not 'LEDmg' in effect_name):
            return f"{self._format_bonus_value(bonus_value)}% 中型能量炮台伤害加成"
        # 检查是否是小型能量炮台伤害加成
        elif 'SETDmg' in effect_name or 'SmallEnergyTurretDamage' in effect_name or 'PBonus' in effect_name or 'HTDmgBonusfixedGC' in effect_name or 'EMTDamageBonus' in effect_name or 'ETDamage' in effect_name or 'SmallEnergy' in effect_name or 'Retribution' in effect_name or bonus_attribute == '伤害量调整' or ('AF' in effect_name and 'Energy' in effect_name):
            return f"{self._format_bonus_value(bonus_value)}% 小型能量炮台伤害加成"
        # 检查是否是小型混合炮台伤害加成
        elif 'HTDmg' in effect_name or 'SmallHybridTurretDamage' in effect_name or 'SmallHybrid' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 小型混合炮台伤害加成"
        # 检查是否是中型混合炮台伤害加成
        elif 'MMDmg' in effect_name or 'MediumHybridTurretDamage' in effect_name or 'MediumHybrid' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 中型混合炮台伤害加成"
        # 检查是否是大型混合炮台伤害加成
        elif 'LMDmg' in effect_name or 'LargeHybridTurretDamage' in effect_name or 'LargeHybrid' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 大型混合炮台伤害加成"
        # 检查是否是小型射弹炮台伤害加成
        elif 'SPTDmg' in effect_name or 'SmallProjectileTurretDamage' in effect_name or 'SmallProjectile' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 小型射弹炮台伤害加成"
        # 检查是否是中型射弹炮台伤害加成
        elif 'MPTDmg' in effect_name or 'MediumProjectileTurretDamage' in effect_name or 'MediumProjectile' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 中型射弹炮台伤害加成"
        # 检查是否是大型射弹炮台伤害加成
        elif 'LPTDmg' in effect_name or 'LargeProjectileTurretDamage' in effect_name or 'LargeProjectile' in effect_name:
            return f"{self._format_bonus_value(bonus_value)}% 大型射弹炮台伤害加成"
        
        # 默认返回通用伤害加成
        return f"{self._format_bonus_value(bonus_value)}% {bonus_attribute}"

    @filter.command("帮助")
    async def help_command(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """EVE ESI 插件帮助

命令列表:
/吉他 <物品名称或ID> - 查询吉他市场价格
/jt <物品名称或ID> - 查询吉他市场价格（短命令）
/属性 <物品名称或ID> - 查看物品具体信息
/简称 <全称>=<简称> - 添加简称
/简称列表 [全称或简称] - 查看简称列表
/简称删除 <简称> - 删除简称
/帮助 - 显示此帮助信息

提示:
- 支持中文名称（如三钛合金）、英文名称（如Tritanium）或物品ID（如34）
- 伊甸币（PLEX）查询：由于市场改版暂不支持
- 模糊搜索只支持列出前10个物品，并已自动过滤涂装和蓝图，请使用详细搜索，或自行添加简称
- 一个全称可以有多个简称，搜索任意简称都会自动转换为全称"""
        
        yield event.plain_result(help_text)
