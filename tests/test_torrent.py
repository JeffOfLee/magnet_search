import hashlib

import pytest

from magnet_search.torrent import BencodeError, build_magnet, extract_info_hash, extract_trackers


def test_extract_info_hash_hashes_raw_info_dictionary_bytes():
    info = b"d6:lengthi12345e4:name10:sample.txt12:piece lengthi16384e6:pieces0:e"
    torrent = b"d8:announce14:http://tracker4:info" + info + b"e"

    assert extract_info_hash(torrent) == hashlib.sha1(info).hexdigest()


def test_extract_trackers_reads_announce_field():
    torrent = b"d8:announce14:http://tracker4:infod4:name10:sample.txtee"

    assert extract_trackers(torrent) == ["http://tracker"]


def test_extract_info_hash_requires_info_dictionary():
    with pytest.raises(BencodeError):
        extract_info_hash(b"d4:info0:ee")


def test_extract_trackers_rejects_truncated_string():
    with pytest.raises(BencodeError):
        extract_trackers(b"d8:announce16:http://trackere")


def test_extract_info_hash_rejects_missing_info_terminator():
    with pytest.raises(BencodeError, match="unterminated dictionary"):
        extract_info_hash(b"d4:infod4:name6:sample")


def test_extract_info_hash_rejects_malformed_integer_in_info_dictionary():
    with pytest.raises(BencodeError):
        extract_info_hash(b"d4:infod1:aiXeee")


def test_extract_trackers_reads_announce_list_tiers_and_dedupes():
    torrent = (
        b"d"
        b"8:announce14:http://tracker"
        b"13:announce-list"
        b"l"
        b"l14:http://tracker17:http://backup-onee"
        b"l17:http://backup-one17:http://backup-twoe"
        b"e"
        b"4:infod4:name10:sample.txte"
        b"e"
    )

    assert extract_trackers(torrent) == [
        "http://tracker",
        "http://backup-one",
        "http://backup-two",
    ]


def test_build_magnet_includes_hash_display_name_and_trackers():
    uri = build_magnet(
        info_hash="abc123",
        display_name="Sample File+Plus",
        trackers=["http://tracker/has space+plus"],
    )

    assert uri.startswith("magnet:?xt=urn:btih:abc123")
    assert "dn=Sample%20File%2BPlus" in uri
    assert "tr=http%3A%2F%2Ftracker%2Fhas%20space%2Bplus" in uri
