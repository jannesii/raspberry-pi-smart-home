// Host driver for the production shared executor, used by relay-script tests.
#include <iostream>
#include "RelayExecutor.h"
struct OutputTransport : RelayTransport {
    bool postRelay(const std::string& command) override {
        std::cout << command; return true;
    }
};
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    JsonDocument command;
    if (deserializeJson(command, argv[1])) return 2;
    OutputTransport transport;
    return RelayExecutor::execute(command.as<JsonObjectConst>(), std::stod(argv[2]), transport, argv[3]) ? 0 : 1;
}
