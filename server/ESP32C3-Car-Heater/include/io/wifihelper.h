#pragma once


bool wifiIsConnected();
bool connectWifi(
    String wifiSSID,
    String wifiPassword,
    const IPAddress& wifiStaticIp,
    const IPAddress& wifiGateway,
    const IPAddress& wifiSubnet,
    const IPAddress& wifiDnsPrimary
); 