"""Natural-language regression inputs and non-prebuilt event sequences."""
PROMPTS = [
    '两个人面对面，A 出一记直拳，B 躯干受击后恢复，4秒，全身入镜。',
    'A 连续出拳，B 侧移躲开，再由 B 踢腿反击，A 抬臂格挡，15秒。',
    'B 先出勾拳，A 后撤闪避，然后向 B 躯干反击，15秒。',
    'A 先试探，B 等待，随后双方交换攻防，动作有轻重变化，15秒。',
    '两个人慢速拳法与踢击对练，接触和反应清楚，15秒。',
    'A 向上出拳，B 低头躲开，随后 B 向 A 腹部反击，15秒。',
    'A 连续进攻，B 以格挡和移动防守，反击后双方拉开距离，15秒。',
    '两人先拳法交锋，再交换踢击和防守，15秒。',
    'B先向A头部出直拳，A格挡，接着A踢向B腹部，B受击，15秒。',
    'A和B慢速对练，A左直拳被格挡，B右勾拳被躲开，15秒。',
    'A直拳命中B腹部，然后保持防守，6秒。',
    'A直拳被B格挡，然后保持防守，6秒。',
    'A直拳被B侧闪躲过，然后保持防守，6秒。',
    'A上勾拳，B后撤躲开，接着B左直拳反击，15秒。',
    'B右腿踢向A躯干，A受击，A接着勾拳反击被B格挡，15秒。',
    'A左腿踢击，B躲开，B上勾拳反击被A格挡，15秒。',
    '双方各出三记直拳，均格挡，最后拉开距离，15秒。',
    'A先向左移动，再出直拳，B向右移动躲避后勾拳反击，15秒。',
    'A直拳、勾拳、踢腿连续进攻，B格挡、侧闪、受击依次应对，15秒。',
    '两人先等待观察，再B向A腹部直拳，然后A头部上勾拳，15秒。',
]


def fixture(job_id='ik_fixture', duration=4):
    return {'version': 1, 'job_id': job_id, 'duration_seconds': duration, 'fps': 30,
            'events': [{'kind': 'strike', 'actor': 'A', 'motion': 'jab', 'region': 'body',
                        'outcome': 'hit', 'start': .5, 'peak': 1.2, 'end': 2.1},
                       {'kind': 'strike', 'actor': 'B', 'motion': 'uppercut', 'region': 'head',
                        'outcome': 'block', 'start': 2.2, 'peak': 2.9, 'end': 3.8}]}
