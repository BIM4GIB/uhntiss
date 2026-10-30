// Generic DPF plug-in around a Pocket voice. The including Plugin.cpp provides, before this file:
//   the device's proto/voice.h (Voice, Params, voice_init, voice_tick), PocketParams.h (generated),
//   and POCKET_LABEL / POCKET_DESCRIPTION / POCKET_VERSION_MINOR / POCKET_ID0 / POCKET_ID1.
#include "DistrhoPlugin.hpp"
#include "mono_notes.h"

START_NAMESPACE_DISTRHO

class PocketPlugin : public Plugin {
public:
    PocketPlugin() : Plugin(POCKET_PARAM_COUNT, POCKET_PRESET_COUNT, 0), sr(getSampleRate()) {
        voice_init(&voice);
        mono_init(&notes);
        for (int i = 0; i < POCKET_PARAM_COUNT; ++i) setParameterValue(i, POCKET_PARAMS[i].def);
    }

protected:
    const char* getLabel() const override { return POCKET_LABEL; }
    const char* getDescription() const override { return POCKET_DESCRIPTION; }
    const char* getMaker() const override { return "UhnTiss"; }
    const char* getHomePage() const override { return "https://github.com/BIM4GIB/uhntiss"; }
    const char* getLicense() const override { return "MIT"; }
    uint32_t getVersion() const override { return d_version(0, POCKET_VERSION_MINOR, 0); }
    int64_t getUniqueId() const override { return d_cconst('P', 'k', POCKET_ID0, POCKET_ID1); }

    void initAudioPort(bool input, uint32_t index, AudioPort& port) override {
        port.groupId = kPortGroupStereo;
        Plugin::initAudioPort(input, index, port);
    }

    void initParameter(uint32_t index, Parameter& parameter) override {
        const PocketParamMeta& m = POCKET_PARAMS[index];
        parameter.hints = kParameterIsAutomatable;
        parameter.name = m.name;
        parameter.symbol = m.symbol;
        parameter.unit = m.unit;
        parameter.ranges.min = m.min;
        parameter.ranges.max = m.max;
        parameter.ranges.def = m.def;
        if (m.is_int) {
            parameter.hints |= kParameterIsInteger;
            ParameterEnumerationValue* const values = new ParameterEnumerationValue[m.enum_count];
            for (int i = 0; i < m.enum_count; ++i) { values[i].value = (float)i; values[i].label = m.enums[i]; }
            parameter.enumValues.count = m.enum_count;
            parameter.enumValues.restrictedMode = true;
            parameter.enumValues.values = values;
        }
    }

    void initProgramName(uint32_t index, String& name) override { name = POCKET_PRESET_NAMES[index]; }

    float getParameterValue(uint32_t index) const override { return ctrl[index]; }

    void setParameterValue(uint32_t index, float value) override {
        ctrl[index] = value;
        *pocket_param_ptr(&params, (int)index) = pocket_ctrl_to_gen((int)index, value);
    }

    void loadProgram(uint32_t index) override {
        for (int i = 0; i < POCKET_PARAM_COUNT; ++i) setParameterValue(i, POCKET_PRESETS[index][i]);
    }

    void activate() override { voice_init(&voice); mono_init(&notes); }
    void sampleRateChanged(double newSampleRate) override { sr = newSampleRate; }

    void run(const float**, float** outputs, uint32_t frames, const MidiEvent* midiEvents, uint32_t midiEventCount) override {
        float* const outL = outputs[0];
        float* const outR = outputs[1];
        uint32_t ev = 0;
        for (uint32_t i = 0; i < frames; ++i) {
            while (ev < midiEventCount && midiEvents[ev].frame <= i) handle(midiEvents[ev++]);
            double led;
            const float s = (float)voice_tick(&voice, notes.in1, notes.in2, notes.in3, notes.in4, notes.in5, &params, sr, &led);
            outL[i] = s;
            outR[i] = s;
        }
        while (ev < midiEventCount) handle(midiEvents[ev++]);
    }

private:
    Voice voice;
    Params params;
    MonoNotes notes;
    float ctrl[POCKET_PARAM_COUNT];
    double sr;

    void handle(const MidiEvent& e) {
        if (e.size < 3) return;
        const uint8_t status = e.data[0] & 0xF0;
        if (status == 0x90 && e.data[2] > 0) mono_note_on(&notes, e.data[1], e.data[2]);
        else if (status == 0x80 || status == 0x90) mono_note_off(&notes, e.data[1]);
        else if (status == 0xB0 && (e.data[1] == 120 || e.data[1] == 123)) mono_all_off(&notes);
    }

    DISTRHO_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(PocketPlugin)
};

Plugin* createPlugin() { return new PocketPlugin(); }

END_NAMESPACE_DISTRHO
