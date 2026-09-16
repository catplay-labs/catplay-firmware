use crate::Radio;

macro_rules! concat_nl {
    ($($x:expr),* $(,)?) => {
        concat!($($x, "\n"),*)
    };
}

pub const HOSTAPD_ULTRA: &str = concat_nl!(
    "interface=wlan0",
    "driver=nl80211",
    "channel=48",
    "ieee80211n=1",
    "ieee80211ac=1",
    "ieee80211ax=1",
    "vht_oper_chwidth=0",
    "he_oper_chwidth=0",
    "hw_mode=a",
    "ignore_broadcast_ssid=0",
    "wowlan_triggers=any",
    "rsn_pairwise=CCMP",
    "ssid=VehiConn_9DFC",
    "wpa=2",
    "wpa_passphrase=12345678",
    "ap_max_inactivity=10",
    "he_basic_mcs_nss_set=65532",
    "vendor_elements=DD4500A0400000020022010A424D57436172506C61790203424D57030F4632352D4E425445766F2D3037313604030001A90606E03501B89DFC0706E03501B89DFCDD0400A04000",
    "",
    "assocresp_elements=7f0400000080",
    "beacon_int=25",
    "dtim_period=1"
);

// Tested for: BCM4358
pub const HOSTAPD_IMX: &str = concat_nl!(
    "",
    "# Basic configuration",
    "",
    "interface=wlan0",
    "ssid=C2A",
    "",
    "# WPA2-only (CarPlay mandates WPA2-PSK/CCMP)",
    "",
    "macaddr_acl=0",
    "auth_algs=1",
    "ignore_broadcast_ssid=0",
    "wpa=2",
    "wpa_passphrase=pass1234",
    "wpa_key_mgmt=WPA-PSK",
    "rsn_pairwise=CCMP",
    "",
    "driver=nl80211",
    "ieee80211n=1",
    "ieee80211ac=0",
    "country_code=US",
    "wmm_enabled=1",
    "#wme_enabled=1",
    "#hw_mode=g",
    "#channel=11",
    "hw_mode=a",
    "channel=36",
    "require_ht=1",
    "require_vht=0",
    "",
    "#ht_capab=[SHORT-GI-20]",
    "#ht_capab=[SHORT-GI-20][SHORT-GI-40][HT40+]",
    "#ht_capab=[SHORT-GI-20][SHORT-GI-40][HT40-]",
    "#vht_capab=[SHORT-GI-80][SHORT-GI-160]",
    "",
);

pub const P2P_ULTRA: &str = concat_nl!(
    "ctrl_interface=/var/run/wpa_supplicant",
    "disable_scan_offload=1",
    "update_config=0",
    "country=CN",
    "device_name=box",
    "device_type=1-0050F204-1",
    "config_methods=virtual_push_button",
    "p2p_go_intent=15",
    "persistent_reconnect=1",
    "p2p_listen_reg_class=81",
    "p2p_listen_channel=6",
    "p2p_oper_reg_class=115",
    "p2p_oper_channel=48",
    "p2p_no_group_iface=1",
    "",
    "network={",
    "    ssid=\"C2A_P2P\"",
    "    psk=\"1234qwer\"",
    "",
    "    key_mgmt=WPA-PSK",
    "    proto=RSN",
    "    pairwise=CCMP",
    "    auth_alg=OPEN",
    "",
    "    mode=3",
    "    disabled=2",
    "    p2p_client_list=ff:ff:ff:ff:ff:ff",
    "}",
);

pub fn match_hostapd_template(radio: Radio) -> &'static str {
    match radio {
        Radio::AIC8800D80 => HOSTAPD_ULTRA,
        _ => HOSTAPD_IMX,
    }
}
