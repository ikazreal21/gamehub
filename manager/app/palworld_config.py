"""PalWorldSettings.ini helpers.

Palworld stores settings as a single line:
  [/Script/Pal.PalGameWorldSettings]
  OptionSettings=(Difficulty="None",DayTimeSpeedRate=1.000000,...)

This module parses that into a dict for the UI form and serializes back.
"""
import re


def parse_option_settings(content: str) -> dict[str, str]:
    """Extract key=value pairs from OptionSettings=(...). Returns {} if not found."""
    m = re.search(r"OptionSettings=\((.*)\)\s*$", content, re.MULTILINE | re.DOTALL)
    if not m:
        return {}
    inner = m.group(1)
    # split on commas not inside quotes
    parts: list[str] = []
    cur, in_q = "", False
    for ch in inner:
        if ch == '"':
            in_q = not in_q
            cur += ch
        elif ch == "," and not in_q:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    out: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def dump_option_settings(settings: dict[str, str]) -> str:
    """Serialize dict back. Numbers/bools unquoted like the game expects is tricky;
    we quote only known string keys, matching community tooling behavior."""
    string_keys = {
        "Difficulty", "DeathPenalty", "ServerName", "ServerDescription",
        "AdminPassword", "ServerPassword", "Region", "BanListURL",
    }
    items = []
    for k, v in settings.items():
        v = v.strip()
        if k in string_keys:
            items.append(f'{k}="{v}"')
        else:
            # keep as-is; if user typed quotes strip them for non-strings
            items.append(f"{k}={v.strip(chr(34))}")
    return "[/Script/Pal.PalGameWorldSettings]\nOptionSettings=(" + ",".join(items) + ")\n"


DEFAULT_PALWORLD_SETTINGS = (
    '[/Script/Pal.PalGameWorldSettings]\n'
    'OptionSettings=(Difficulty="None",DayTimeSpeedRate=1.000000,'
    'NightTimeSpeedRate=1.000000,ExpRate=1.000000,PalCaptureRate=1.000000,'
    'PalSpawnNumRate=1.000000,PalDamageRateAttack=1.000000,PalDamageRateDefense=1.000000,'
    'PlayerDamageRateAttack=1.000000,PlayerDamageRateDefense=1.000000,'
    'PlayerStomachDecreaceRate=1.000000,PlayerStaminaDecreaceRate=1.000000,'
    'PlayerAutoHPRegeneRate=1.000000,PlayerAutoHpRegeneRateInSleep=1.000000,'
    'PalStomachDecreaceRate=1.000000,PalStaminaDecreaceRate=1.000000,'
    'PalAutoHPRegeneRate=1.000000,PalAutoHpRegeneRateInSleep=1.000000,'
    'BuildObjectDamageRate=1.000000,BuildObjectDeteriorationDamageRate=1.000000,'
    'CollectionDropRate=1.000000,CollectionObjectHpRate=1.000000,CollectionObjectRespawnSpeedRate=1.000000,'
    'EnemyDropItemRate=1.000000,DeathPenalty="Item",bEnablePlayerToPlayerDamage=False,'
    'bEnableFriendlyFire=False,bEnableInvaderEnemy=True,bActiveUNKO=False,'
    'bEnableAimAssistPad=True,bEnableAimAssistKeyboard=False,DropItemMaxNum=3000,'
    'DropItemMaxNum_UNKO=100,BaseCampMaxNum=128,BaseCampWorkerMaxNum=15,'
    'DropItemAliveMaxHours=1.000000,bAutoResetGuildNoOnlinePlayers=False,'
    'AutoResetGuildTimeNoOnlinePlayers=72.000000,GuildPlayerMaxNum=20,'
    'PalEggDefaultHatchingTime=72.000000,WorkSpeedRate=1.000000,'
    'bIsMultiplay=False,bIsPvP=False,bCanPickupOtherGuildDeathPenaltyDrop=False,'
    'bEnableNonLoginPenalty=True,bEnableFastTravel=True,bIsStartLocationSelectByMap=True,'
    'bExistPlayerAfterLogout=False,bEnableDefenseOtherGuildPlayer=False,'
    'CoopPlayerMaxNum=4,ServerPlayerMaxNum=32,ServerName="GameHub Palworld",'
    'ServerDescription="Managed by GameHub",AdminPassword="changeme-admin",'
    'ServerPassword="",PublicPort=8211,PublicIP="",RCONEnabled=True,RCONPort=25575,'
    'Region="",bUseAuth=True,BanListURL="https://api.palworldgame.com/api/banlist.txt")\n'
)
