Compare two Hearthstone card effects.

Effect A is the baseline. Effect B is the candidate.

Each card description begins with its printed Mana and, for characters,
Attack and Health. Lower Mana and higher Attack or Health are improvements.
The remaining text is its card effect; B must preserve A's value while adding
or improving it, without a compensating drawback.

Return `YES` only if B is a clear upgrade of A.

Otherwise return `NO`. When uncertain, return `NO`.

Examples:

A: `NONE`
B: `<b>Taunt</b>`
Result: `YES` - anything is better than `NONE`

A: `<b>Taunt</b>`
B: `<b>Taunt</b> <b>Divine Shield</b>`
Result: `YES` - more keyword is better (keywords are phrases inside <b>...</b>)

A: `<b>Lifesteal</b>`
B: `<b>Taunt</b> <b>Lifesteal</b>`
Result: `YES` - more keyword is better (keywords are phrases inside <b>...</b>)

A: `Deal 3 damage.`
B: `Deal 4 damage.`
Result: `YES` - more higher values

A: `<b>Battlecry:</b> Deal 3 damage to a minion.`
B: `<b>Battlecry:</b> Deal 3 damage to a minion. If it dies, gain 5 Armor.`
Result: `YES` - additional effect

A: `Add a random Beast to your hand.`
B: `Get a random Beast. Reduce its Cost by (1).`
Result: `YES` - additional cost reduction 

A: `Draw 2 cards.`
B: `Draw 2 cards. Restore 5 Health to your hero.`
Result: `YES` - additional effect

A: `Add a random Mage spell to your hand.`
B: `<b>Discover</b> a Mage spell.`
Result: `YES` - Discover allows choice, choice is better than random

A: `If you control a Beast, draw 2 cards.`
B: `Draw 2 cards.`
Result: `YES` - unconditional effect is better

A: `Deal 5 damage to a minion.`
B: `<b>Choose One -</b> Gain 5 Armor; or Deal 5 damage to a minion.`
Result: `YES` - additional choice is better

A: `Deal 3 damage to a random enemy.`
B: `Deal 3 damage to an enemy.`
Result: `YES` — `an` enemy implies a choice, choice is better than random.

A: `Deal 2 damage Destroy one of your Mana Crystals`
B: `Deal 2 damage.`
Result: `YES` — destroying own resources is worse.

A: `Destroy enemy minion and your minion.`
B: `Destroy enemy minion.`
Result: `YES` — destroying own resources is worse.

A: `Deal 5 damage`
B: `Deal 5 damage. <b>Outcast:</b> This costs 1.`
Result: `YES` - has potential to set fixed cost (usually lower than default)

A: `Draw 2 cards. Take 2 damage.`
B: `Draw 3 cards. Take 5 damage.`
Result: `NO` — the stronger draw comes with a stronger drawback.

A: `<b>Deathrattle:</b> Deal 2 damage to a random enemy.`
B: `<b>Battlecry:</b> Deal 10 damage to a random enemy.`
Result: `NO` — the trigger keyword is different.

A: `Draw 2 cards. Take 2 damage.`
B: `Draw 3 cards. Take 5 damage.`
Result: `NO` — the stronger draw comes with a stronger drawback.

Now evaluate this pair:

A: `{effect_A}`
B: `{effect_B}`

Return exactly this JSON object:
{"is_effect_better":"YES or NO","justification":"brief reason"}
