#pragma once

#include <Arduino.h>
#include <WebSocketsClient.h>
#include <functional>

#include "io/ShellyHandler.h"
#include "core/LogManager.h"

/**
 * WebSocket client task for real-time communication with the server.
 * 
 * Connects to wss.jannenkoti.com and:
 * - Receives commands instantly (turn_on, turn_off, etc.)
 * - Sends status updates and heartbeats
 * - Auto-reconnects with exponential backoff
 * 
 * Runs as a separate FreeRTOS task alongside PosterTask (HTTP fallback).
 */
class WebSocketTask
{
public:
    WebSocketTask(ShellyHandler &shelly, LogManager &logManager);

    /**
     * Start the FreeRTOS task.
     * @param stackSize Stack size in bytes (default 8192 for SSL)
     * @param priority Task priority (default 1)
     */
    void start(uint32_t stackSize = 8192, UBaseType_t priority = 1);

    /**
     * Stop the task and disconnect.
     */
    void stop();

    /**
     * Check if WebSocket is currently connected.
     */
    bool isConnected() const { return connected_; }

    /**
     * Check if WebSocket is connected and authenticated, so it is safe to use
     * as the primary transport.
     */
    bool isReady() const { return connected_ && authenticated_; }

    /**
     * Get connection statistics.
     */
    struct Stats {
        uint32_t connectCount;
        uint32_t disconnectCount;
        uint32_t messagesReceived;
        uint32_t messagesSent;
        uint32_t commandsExecuted;
        uint32_t lastConnectedMs;
        uint32_t uptimeMs;
    };
    Stats getStats() const;

    /**
     * Queue a status update to be sent over WebSocket.
     * Thread-safe.
     */
    void queueStatusUpdate();

    /**
     * Check if a command was recently executed via WebSocket.
     * Used by PosterTask to avoid duplicate execution.
     * @param action Command action name
     * @param withinMs Time window in milliseconds
     * @return true if command was executed within the time window
     */
    bool wasCommandExecutedRecently(const char* action, uint32_t withinMs = 5000);

    TaskHandle_t handle() const { return handle_; }

private:
    // Task entry trampoline
    static void taskEntry(void *pvParameters);

    // Main task loop
    void run();

    // WebSocket event handler
    void onWebSocketEvent(WStype_t type, uint8_t *payload, size_t length);
    static void webSocketEventStatic(WStype_t type, uint8_t *payload, size_t length);

    // Connection management
    void connect();
    void disconnect();
    void sendAuthentication();
    
    // Message handling
    void processMessage(const char* payload, size_t length);
    void processCommands(const char* jsonArray, size_t length);
    
    // Command execution (shared with PosterTask)
    void executeCommand(const char* action, const char* source = nullptr);
    void handleTurnOn();
    void handleTurnOff();
    void handleGetLogs();
    void handleEspRestart();
    void handleShellyReboot();

    // Status sending
    void sendStatus();
    void sendHeartbeat();
    void sendActionResult(const char* action, bool success, const char* note = nullptr);

    // Helpers
    String log(const String &msg) const;
    String buildStatusJson();

    // Dependencies
    ShellyHandler &shelly_;
    LogManager &logger_;

    // Task handle
    TaskHandle_t handle_ = nullptr;
    volatile bool running_ = false;

    // WebSocket client
    WebSocketsClient webSocket_;
    volatile bool connected_ = false;
    volatile bool authenticated_ = false;

    // Configuration
    static constexpr const char* WS_HOST = "wss.jannenkoti.com";
    static constexpr uint16_t WS_PORT = 443;
    static constexpr const char* WS_PATH = "/ws";
    
    // Reconnection with exponential backoff
    uint32_t reconnectDelayMs_ = 1000;
    static constexpr uint32_t RECONNECT_DELAY_MIN_MS = 1000;
    static constexpr uint32_t RECONNECT_DELAY_MAX_MS = 60000;

    // Heartbeat
    static constexpr uint32_t HEARTBEAT_INTERVAL_MS = 30000;  // 30s (Cloudflare timeout is 100s)
    uint32_t lastHeartbeatMs_ = 0;

    // Status update flag (set by other tasks, cleared after sending)
    volatile bool statusUpdatePending_ = false;
    volatile bool logsRequested_ = false;

    // Command deduplication
    struct RecentCommand {
        char action[32];
        uint32_t executedAtMs;
    };
    static constexpr size_t MAX_RECENT_COMMANDS = 8;
    RecentCommand recentCommands_[MAX_RECENT_COMMANDS];
    size_t recentCommandIndex_ = 0;
    void recordCommandExecution(const char* action);

    // Statistics
    Stats stats_ = {};

    // Static instance pointer for callback
    static WebSocketTask* instance_;
};
