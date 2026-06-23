/*
 * Naruhodo native launcher
 *
 * Sparkle の XPC サービスはメインプロセスのコード署名を検証する。
 * bash → exec python だとプロセスが Python に置き換わり、署名が
 * Developer ID ではなくなるため XPC 接続が拒否される。
 *
 * このネイティブバイナリが CFBundleExecutable として署名され、
 * セットアップスクリプトと Python を子プロセスとして起動しつつ
 * 自身は生き続けることで、Sparkle の署名検証が通るようにする。
 */
#include <libgen.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static pid_t child_pid = 0;

static void forward_signal(int sig) {
    if (child_pid > 0) kill(child_pid, sig);
}

int main(int argc, char *argv[]) {
    char exe[PATH_MAX];
    uint32_t size = sizeof(exe);
    if (_NSGetExecutablePath(exe, &size) != 0) {
        fprintf(stderr, "naruhodo: cannot resolve executable path\n");
        return 1;
    }

    char resolved[PATH_MAX];
    if (!realpath(exe, resolved)) {
        fprintf(stderr, "naruhodo: cannot resolve real path\n");
        return 1;
    }

    /* Contents/MacOS/naruhodo → Contents/MacOS */
    char *macos_dir = dirname(resolved);
    /* Contents/MacOS → Contents */
    char contents_dir[PATH_MAX];
    snprintf(contents_dir, sizeof(contents_dir), "%s/..", macos_dir);
    char contents_resolved[PATH_MAX];
    realpath(contents_dir, contents_resolved);
    /* Contents → Bundle root */
    char bundle_dir[PATH_MAX];
    snprintf(bundle_dir, sizeof(bundle_dir), "%s/..", contents_resolved);
    char bundle_resolved[PATH_MAX];
    realpath(bundle_dir, bundle_resolved);

    setenv("NARUHODO_BUNDLE_PATH", bundle_resolved, 1);

    /* setup.sh のパスを構築 */
    char setup_path[PATH_MAX];
    snprintf(setup_path, sizeof(setup_path), "%s/naruhodo-setup.sh", macos_dir);

    /* セットアップスクリプトを実行（同期） */
    pid_t setup_pid = fork();
    if (setup_pid == 0) {
        execl("/bin/bash", "bash", setup_path, NULL);
        _exit(127);
    }
    int setup_status;
    waitpid(setup_pid, &setup_status, 0);
    if (WIFEXITED(setup_status) && WEXITSTATUS(setup_status) != 0) {
        return WEXITSTATUS(setup_status);
    }

    /* Python を子プロセスとして起動 */
    char appdata[PATH_MAX];
    snprintf(appdata, sizeof(appdata), "%s/Library/Application Support/Naruhodo",
             getenv("HOME"));

    char python_path[PATH_MAX];
    snprintf(python_path, sizeof(python_path), "%s/.venv/bin/python", appdata);

    char desktop_path[PATH_MAX];
    snprintf(desktop_path, sizeof(desktop_path), "%s/desktop.py", appdata);

    char log_path[PATH_MAX];
    snprintf(log_path, sizeof(log_path), "%s/naruhodo.log", appdata);

    /* stdout/stderr をログファイルにリダイレクト */
    FILE *logfp = fopen(log_path, "a");
    if (logfp) {
        int logfd = fileno(logfp);
        dup2(logfd, STDOUT_FILENO);
        dup2(logfd, STDERR_FILENO);
    }

    child_pid = fork();
    if (child_pid == 0) {
        execl(python_path, "python", "-u", desktop_path, NULL);
        perror("naruhodo: failed to exec python");
        _exit(127);
    }

    /* シグナルを子プロセスに転送 */
    signal(SIGTERM, forward_signal);
    signal(SIGINT, forward_signal);
    signal(SIGHUP, forward_signal);

    /* 子プロセスの終了を待つ */
    int status;
    waitpid(child_pid, &status, 0);

    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 1;
}
