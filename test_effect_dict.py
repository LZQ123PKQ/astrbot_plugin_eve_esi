from effect_dict import load_effect_descriptions, identify_skill_type, get_effect_description

descriptions = load_effect_descriptions()
print(f"Loaded {len(descriptions)} effect descriptions")

# 测试识别技能类型
print("\nTest identify_skill_type:")
print(f"  shipBonusAF -> {identify_skill_type('shipBonusAF')}")
print(f"  shipBonusABC -> {identify_skill_type('shipBonusABC')}")
print(f"  eliteBonusGunship1 -> {identify_skill_type('eliteBonusGunship1')}")

# 测试获取描述
print("\nTest get_effect_description:")

# 查找包含 shipBonusAF 的 effect
for name in list(descriptions.keys())[:10]:
    print(f"  {name}: {descriptions[name]['template']}")

# 查找实际的 shipBonusAF
test_names = [
    'shipBonusAF',
    'amarrFrigateSkillLevelPreMulShipBonusAFShip',
    'shipBonusSmallEnergyTurretDamageAmaNavyDestroyer',
    'shipSETDmgBonusAF',
]

print("\nTest specific effects:")
for name in test_names:
    if name in descriptions:
        print(f"  {name}: {descriptions[name]['template']}")
        desc = get_effect_description(name, 5.0, descriptions)
        print(f"    -> {desc}")
    else:
        print(f"  {name}: NOT FOUND")
