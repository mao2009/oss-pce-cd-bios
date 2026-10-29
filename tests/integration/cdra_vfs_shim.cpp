// Diagnostic shim for the CD read-ahead A/B comparison (exp/cd-readahead-compare). Linked only into the probe.
// 1) Installs a stdio-backed libretro VFS so `cdrom://<abs path>` cue paths take the streaming
//    (read-ahead-capable) load options in CdRomMedia::LoadCueFromFile. Each VFS read is logged to the file
//    named by CDRA_VFS_LOG with the thread that issued it ("main" = demand, "worker" = read-ahead prefetch).
// 2) Pins time(): the core seeds its RNG with time(NULL) in GeargrafxCore::Init, so CDRA_SEED fixes it.
// Neither hook changes emulated guest state; they only observe I/O and fix the RNG seed.
#include <time.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include "libretro.h"
#include "media_file.h"

namespace {

struct ShimFile {
    FILE* file;
    std::string path;
};

std::mutex g_mutex;
std::thread::id g_main_thread;
std::string g_log_path;

void log_event(const char* op, long long offset, long long bytes) {
    if (g_log_path.empty()) return;
    const char* who = std::this_thread::get_id() == g_main_thread ? "main" : "worker";
    FILE* log = std::fopen(g_log_path.c_str(), "a");
    if (!log) return;
    std::fprintf(log, "VFS {\"thread\":\"%s\",\"op\":\"%s\",\"offset\":%lld,\"bytes\":%lld}\n", who, op, offset, bytes);
    std::fclose(log);
}

retro_vfs_file_handle* shim_open(const char* path, unsigned mode, unsigned) {
    if (!path || (mode & RETRO_VFS_FILE_ACCESS_WRITE)) return nullptr;
    std::string local = std::strncmp(path, "cdrom://", 8) == 0 ? std::string(path + 8) : std::string(path);
    std::lock_guard<std::mutex> lock(g_mutex);
    FILE* file = std::fopen(local.c_str(), "rb");
    if (!file) return nullptr;
    return reinterpret_cast<retro_vfs_file_handle*>(new ShimFile{file, local});
}

const char* shim_get_path(retro_vfs_file_handle* handle) {
    return handle ? reinterpret_cast<ShimFile*>(handle)->path.c_str() : nullptr;
}

int shim_close(retro_vfs_file_handle* handle) {
    if (!handle) return -1;
    ShimFile* file = reinterpret_cast<ShimFile*>(handle);
    std::lock_guard<std::mutex> lock(g_mutex);
    int result = std::fclose(file->file);
    delete file;
    return result;
}

int64_t shim_size(retro_vfs_file_handle* handle) {
    ShimFile* file = reinterpret_cast<ShimFile*>(handle);
    std::lock_guard<std::mutex> lock(g_mutex);
    long long here = ftello(file->file);
    fseeko(file->file, 0, SEEK_END);
    long long size = ftello(file->file);
    fseeko(file->file, here, SEEK_SET);
    return size;
}

int64_t shim_tell(retro_vfs_file_handle* handle) {
    std::lock_guard<std::mutex> lock(g_mutex);
    return ftello(reinterpret_cast<ShimFile*>(handle)->file);
}

int64_t shim_seek(retro_vfs_file_handle* handle, int64_t offset, int whence) {
    static const int map[] = {SEEK_SET, SEEK_CUR, SEEK_END};
    if (whence < 0 || whence > 2) return -1;
    std::lock_guard<std::mutex> lock(g_mutex);
    FILE* file = reinterpret_cast<ShimFile*>(handle)->file;
    if (fseeko(file, offset, map[whence]) != 0) return -1;
    return ftello(file);
}

int64_t shim_read(retro_vfs_file_handle* handle, void* buffer, uint64_t length) {
    ShimFile* file = reinterpret_cast<ShimFile*>(handle);
    std::lock_guard<std::mutex> lock(g_mutex);
    long long offset = ftello(file->file);
    size_t got = std::fread(buffer, 1, (size_t)length, file->file);
    log_event("read", offset, (long long)got);
    return (int64_t)got;
}

int64_t shim_write(retro_vfs_file_handle*, const void*, uint64_t) { return -1; }
int shim_flush(retro_vfs_file_handle*) { return 0; }

retro_vfs_interface g_iface = {};

struct Installer {
    Installer() {
        g_main_thread = std::this_thread::get_id();
        const char* log = std::getenv("CDRA_VFS_LOG");
        if (log) g_log_path = log;
        g_iface.get_path = shim_get_path;
        g_iface.open = shim_open;
        g_iface.close = shim_close;
        g_iface.size = shim_size;
        g_iface.tell = shim_tell;
        g_iface.seek = shim_seek;
        g_iface.read = shim_read;
        g_iface.write = shim_write;
        g_iface.flush = shim_flush;
        MediaFile::SetVfsInterface(&g_iface);
    }
} g_installer;

}  // namespace

// Called by the probe (built with -DCDRA_VFS_MARK) just before the guest runs; splits load-time I/O from guest I/O.
extern "C" void cdra_mark(const char* name) {
    if (g_log_path.empty()) return;
    std::lock_guard<std::mutex> lock(g_mutex);
    FILE* log = std::fopen(g_log_path.c_str(), "a");
    if (!log) return;
    std::fprintf(log, "MARK {\"name\":\"%s\"}\n", name);
    std::fclose(log);
}

extern "C" time_t time(time_t* out) noexcept {
    const char* seed = std::getenv("CDRA_SEED");
    time_t value = seed ? (time_t)std::strtoll(seed, nullptr, 10) : (time_t)0;
    if (out) *out = value;
    return value;
}
