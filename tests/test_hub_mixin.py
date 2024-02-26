import inspect
import json
import os
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union
from unittest.mock import Mock, patch

import pytest

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.hub_mixin import ModelHubMixin
from huggingface_hub.utils import SoftTemporaryDirectory

from .testing_constants import ENDPOINT_STAGING, TOKEN, USER
from .testing_utils import repo_name


@dataclass
class ConfigAsDataclass:
    foo: int = 10
    bar: str = "baz"


CONFIG_AS_DATACLASS = ConfigAsDataclass(foo=20, bar="qux")
CONFIG_AS_DICT = {"foo": 20, "bar": "qux"}


class BaseModel:
    def _save_pretrained(self, save_directory: Path) -> None:
        return

    @classmethod
    def _from_pretrained(
        cls,
        model_id: Union[str, Path],
        **kwargs,
    ) -> "BaseModel":
        # Little hack but in practice NO-ONE is creating 5 inherited classes for their framework :D
        if inspect.signature(cls.__init__).parameters.get("config"):
            return cls(config=kwargs.get("config"))
        return cls()


class DummyModelNoConfig(BaseModel, ModelHubMixin):
    def __init__(self):
        pass


class DummyModelConfigAsDataclass(BaseModel, ModelHubMixin):
    def __init__(self, config: ConfigAsDataclass):
        pass


class DummyModelConfigAsDict(BaseModel, ModelHubMixin):
    def __init__(self, config: Dict):
        pass


class DummyModelConfigAsOptionalDataclass(BaseModel, ModelHubMixin):
    def __init__(self, config: Optional[ConfigAsDataclass] = None):
        pass


class DummyModelConfigAsOptionalDict(BaseModel, ModelHubMixin):
    def __init__(self, config: Optional[Dict] = None):
        pass


@pytest.mark.usefixtures("fx_cache_dir")
class HubMixinTest(unittest.TestCase):
    cache_dir: Path

    @classmethod
    def setUpClass(cls):
        """
        Share this valid token in all tests below.
        """
        cls._api = HfApi(endpoint=ENDPOINT_STAGING, token=TOKEN)

    def assert_valid_config_json(self) -> None:
        # config.json saved correctly
        with open(self.cache_dir / "config.json") as f:
            assert json.load(f) == CONFIG_AS_DICT

    def assert_no_config_json(self) -> None:
        # config.json not saved
        files = os.listdir(self.cache_dir)
        assert "config.json" not in files

    def test_save_pretrained_no_config(self):
        model = DummyModelNoConfig()
        model.save_pretrained(self.cache_dir)
        self.assert_no_config_json()

    def test_save_pretrained_as_dataclass_basic(self):
        model = DummyModelConfigAsDataclass(CONFIG_AS_DATACLASS)
        model.save_pretrained(self.cache_dir)
        self.assert_valid_config_json()

    def test_save_pretrained_as_dict_basic(self):
        model = DummyModelConfigAsDict(CONFIG_AS_DICT)
        model.save_pretrained(self.cache_dir)
        self.assert_valid_config_json()

    def test_save_pretrained_optional_dataclass(self):
        model = DummyModelConfigAsOptionalDataclass()
        model.save_pretrained(self.cache_dir)
        self.assert_no_config_json()

        model = DummyModelConfigAsOptionalDataclass(CONFIG_AS_DATACLASS)
        model.save_pretrained(self.cache_dir)
        self.assert_valid_config_json()

    def test_save_pretrained_optional_dict(self):
        model = DummyModelConfigAsOptionalDict()
        model.save_pretrained(self.cache_dir)
        self.assert_no_config_json()

        model = DummyModelConfigAsOptionalDict(CONFIG_AS_DICT)
        model.save_pretrained(self.cache_dir)
        self.assert_valid_config_json()

    def test_save_pretrained_with_dataclass_config(self):
        model = DummyModelConfigAsOptionalDataclass()
        model.save_pretrained(self.cache_dir, config=CONFIG_AS_DATACLASS)
        self.assert_valid_config_json()

    def test_save_pretrained_with_dict_config(self):
        model = DummyModelConfigAsOptionalDict()
        model.save_pretrained(self.cache_dir, config=CONFIG_AS_DICT)
        self.assert_valid_config_json()

    def test_save_pretrained_with_push_to_hub(self):
        repo_id = repo_name("save")
        save_directory = self.cache_dir / repo_id

        mocked_model = DummyModelConfigAsDataclass(CONFIG_AS_DATACLASS)
        mocked_model.push_to_hub = Mock()
        mocked_model._save_pretrained = Mock()  # disable _save_pretrained to speed-up

        # Not pushed to hub
        mocked_model.save_pretrained(save_directory)
        mocked_model.push_to_hub.assert_not_called()

        # Push to hub with repo_id (config is pushed)
        mocked_model.save_pretrained(save_directory, push_to_hub=True, repo_id="CustomID")
        mocked_model.push_to_hub.assert_called_with(repo_id="CustomID", config=CONFIG_AS_DICT)

        # Push to hub with default repo_id (based on dir name)
        mocked_model.save_pretrained(save_directory, push_to_hub=True)
        mocked_model.push_to_hub.assert_called_with(repo_id=repo_id, config=CONFIG_AS_DICT)

    @patch.object(DummyModelNoConfig, "_from_pretrained")
    def test_from_pretrained_model_id_only(self, from_pretrained_mock: Mock) -> None:
        model = DummyModelNoConfig.from_pretrained("namespace/repo_name")
        from_pretrained_mock.assert_called_once()
        assert model is from_pretrained_mock.return_value

    @patch.object(DummyModelNoConfig, "_from_pretrained")
    def test_from_pretrained_model_id_and_revision(self, from_pretrained_mock: Mock) -> None:
        """Regression test for #1313.
        See https://github.com/huggingface/huggingface_hub/issues/1313."""
        model = DummyModelNoConfig.from_pretrained("namespace/repo_name", revision="123456789")
        from_pretrained_mock.assert_called_once_with(
            model_id="namespace/repo_name",
            revision="123456789",  # Revision is passed correctly!
            cache_dir=None,
            force_download=False,
            proxies=None,
            resume_download=False,
            local_files_only=False,
            token=None,
        )
        assert model is from_pretrained_mock.return_value

    def test_from_pretrained_from_relative_path(self):
        with SoftTemporaryDirectory(dir=Path(".")) as tmp_relative_dir:
            relative_save_directory = Path(tmp_relative_dir) / "model"
            DummyModelConfigAsDataclass(config=CONFIG_AS_DATACLASS).save_pretrained(relative_save_directory)
            model = DummyModelConfigAsDataclass.from_pretrained(relative_save_directory)
            assert model.config == CONFIG_AS_DATACLASS

    def test_from_pretrained_from_absolute_path(self):
        save_directory = self.cache_dir / "subfolder"
        DummyModelConfigAsDataclass(config=CONFIG_AS_DATACLASS).save_pretrained(save_directory)
        model = DummyModelConfigAsDataclass.from_pretrained(save_directory)
        assert model.config == CONFIG_AS_DATACLASS

    def test_from_pretrained_from_absolute_string_path(self):
        save_directory = str(self.cache_dir / "subfolder")
        DummyModelConfigAsDataclass(config=CONFIG_AS_DATACLASS).save_pretrained(save_directory)
        model = DummyModelConfigAsDataclass.from_pretrained(save_directory)
        assert model.config == CONFIG_AS_DATACLASS

    def test_push_to_hub(self):
        repo_id = f"{USER}/{repo_name('push_to_hub')}"
        DummyModelConfigAsDataclass(CONFIG_AS_DATACLASS).push_to_hub(repo_id=repo_id, token=TOKEN)

        # Test model id exists
        self._api.model_info(repo_id)

        # Test config has been pushed to hub
        tmp_config_path = hf_hub_download(
            repo_id=repo_id,
            filename="config.json",
            use_auth_token=TOKEN,
            cache_dir=self.cache_dir,
        )
        with open(tmp_config_path) as f:
            assert json.load(f) == CONFIG_AS_DICT

        # from_pretrained with correct serialization
        from_pretrained_kwargs = {
            "pretrained_model_name_or_path": repo_id,
            "cache_dir": self.cache_dir,
            "api_endpoint": ENDPOINT_STAGING,
            "token": TOKEN,
        }
        for cls in (DummyModelConfigAsDataclass, DummyModelConfigAsOptionalDataclass):
            assert cls.from_pretrained(**from_pretrained_kwargs).config == CONFIG_AS_DATACLASS

        for cls in (DummyModelConfigAsDict, DummyModelConfigAsOptionalDict):
            assert cls.from_pretrained(**from_pretrained_kwargs).config == CONFIG_AS_DICT

        # Delete repo
        self._api.delete_repo(repo_id=repo_id)
