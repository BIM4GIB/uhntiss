// Pocket 101 as a plug-in: the M4L device's C voice mirror wrapped by common/PocketPlugin.cpp.
#include "../../../m4l/pocket101/proto/voice.h"
#include "PocketParams.h"
#define POCKET_LABEL "Pocket101"
#define POCKET_DESCRIPTION "SH-101 style mono bass: saw/PWM/sub/noise mixer, OTA ladder, ADSR, LFO, portamento"
#define POCKET_VERSION_MINOR 1
#define POCKET_ID0 '0'
#define POCKET_ID1 '1'
#include "../common/PocketPlugin.cpp"
