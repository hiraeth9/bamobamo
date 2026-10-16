"""Character selection routing; real skin/rig behavior is checked in Blender."""
from pathlib import Path
import sys
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import agent_character as character


def test_available_local_beta_remains_default():
    with patch.object(character,'_catalog_character',return_value={'character_id':'beta'}) as load:
        assert character.default_character_id()=='beta'
        load.assert_called_once_with('beta')


def test_clean_clone_without_beta_selects_installed_cc0_profile():
    def load(identifier):
        if identifier=='beta':raise ValueError('人物源资产缺失')
        return {'character_id':identifier}
    with patch.object(character,'_catalog_character',side_effect=load):
        assert character.default_character_id()=='quaternius_mannequin_f'


def test_explicit_missing_beta_errors_without_silent_substitution():
    with patch.object(character,'_catalog_character',side_effect=ValueError('人物源资产缺失')), patch.object(character,'default_character_id') as fallback:
        with pytest.raises(ValueError,match='人物源资产缺失'):
            character.character_info('beta',detail=True)
        fallback.assert_not_called()
