// Lattice Digest notifier.
//
// Replaces the Script Editor applet. An exported applet's `display notification`
// is attributed to Script Editor, not to the applet, so clicking the banner
// opened Script Editor's file panel. Re-signing the applet so its code-signing
// identifier matched CFBundleIdentifier did not change that: on this OS the
// AppleScript host owns the notification regardless of how the applet is signed.
//
// A real bundle posting through UNUserNotificationCenter owns its notifications,
// so clicking one launches THIS app, which opens the digest.
//
// Objective-C rather than Swift on purpose: this machine's Command Line Tools
// ship a duplicate SwiftBridging module map, so every swiftc build fails.

#import <Cocoa/Cocoa.h>
#import <UserNotifications/UserNotifications.h>
#import <netinet/in.h>
#import <sys/socket.h>
#import <unistd.h>

static NSString *DigestPath(void) {
    return [@"~/Lattice/lattice_data/digest/latest.html" stringByExpandingTildeInPath];
}
static NSString *MarkdownPath(void) {
    return [@"~/Lattice/lattice_data/digest/latest.md" stringByExpandingTildeInPath];
}
static void Note(NSString *s) {
    fprintf(stderr, "lattice-digest: %s\n", s.UTF8String);
}
// The interactive page lives on a local server; the written file is the fallback
// when that server is not up, so a click always shows today's list either way.
static BOOL ServerUp(void) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return NO;
    struct timeval tv = {0, 250000};   // 250ms; it is loopback or it is nothing
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(8787);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    BOOL up = connect(fd, (struct sockaddr *)&addr, sizeof addr) == 0;
    close(fd);
    return up;
}

static NSString *DigestURL(void) {
    NSString *tokenPath = [@"~/Lattice/lattice_data/.digest-token" stringByExpandingTildeInPath];
    NSString *token = [NSString stringWithContentsOfFile:tokenPath
                                                encoding:NSUTF8StringEncoding error:NULL];
    token = [token stringByTrimmingCharactersInSet:
             NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!token.length) return nil;
    NSString *safe = [token stringByAddingPercentEncodingWithAllowedCharacters:
                      NSCharacterSet.URLQueryAllowedCharacterSet];
    return [NSString stringWithFormat:@"http://127.0.0.1:8787/?t=%@", safe];
}

static void OpenDigest(void) {
    NSString *live = DigestURL();
    if (live && ServerUp()) {
        [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:live]];
        return;
    }
    Note(@"digest server not reachable on 127.0.0.1:8787 — opening the static file");
    [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:DigestPath()]];
}

// Line 3 of latest.md is the count line: "40 open · 16 waiting on you · ...".
static NSString *Headline(void) {
    NSString *fallback = @"Today's priorities are ready";
    NSString *text = [NSString stringWithContentsOfFile:MarkdownPath()
                                               encoding:NSUTF8StringEncoding error:NULL];
    if (!text) return fallback;
    NSArray<NSString *> *lines = [text componentsSeparatedByString:@"\n"];
    if (lines.count < 3) return fallback;
    NSString *third = [lines[2] stringByTrimmingCharactersInSet:
                       NSCharacterSet.whitespaceCharacterSet];
    return third.length ? third : fallback;
}

static void QuitAfter(double seconds) {
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(seconds * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{ exit(0); });
}

@interface AppDelegate : NSObject <NSApplicationDelegate, UNUserNotificationCenterDelegate>
@property (nonatomic) BOOL handled;
@end

@implementation AppDelegate

- (void)showOnce {
    if (self.handled) return;
    self.handled = YES;
    OpenDigest();
}

- (void)applicationWillFinishLaunching:(NSNotification *)n {
    UNUserNotificationCenter.currentNotificationCenter.delegate = self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)n {
    if ([NSProcessInfo.processInfo.arguments containsObject:@"notify"]) {
        [self post];
        return;
    }
    // No arguments: either the banner was clicked, or the app was opened by hand.
    // Give the click callback a moment to land first, then open the digest anyway.
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.7 * NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        [self showOnce];
        QuitAfter(0.3);
    });
}

- (void)post {
    UNUserNotificationCenter *center = UNUserNotificationCenter.currentNotificationCenter;
    [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound)
                          completionHandler:^(BOOL granted, NSError *error) {
        if (error) { Note([NSString stringWithFormat:@"authorization error: %@", error]); exit(2); }
        if (!granted) { Note(@"notifications not permitted for this app"); exit(3); }

        UNMutableNotificationContent *content = [UNMutableNotificationContent new];
        content.title = @"Lattice — morning priorities";
        content.subtitle = @"Click to open today's list";
        content.body = Headline();
        content.sound = UNNotificationSound.defaultSound;

        NSString *ident = [NSString stringWithFormat:@"lattice-digest-%ld",
                           (long)NSDate.date.timeIntervalSince1970];
        UNNotificationRequest *req = [UNNotificationRequest requestWithIdentifier:ident
                                                                         content:content
                                                                         trigger:nil];
        [center addNotificationRequest:req withCompletionHandler:^(NSError *err) {
            if (err) { Note([NSString stringWithFormat:@"post failed: %@", err]); exit(4); }
            QuitAfter(1.0);
        }];
    }];
}

// Clicking the banner. Fires whether the app was already running or was
// launched by the click.
- (void)userNotificationCenter:(UNUserNotificationCenter *)center
didReceiveNotificationResponse:(UNNotificationResponse *)response
         withCompletionHandler:(void (^)(void))done {
    [self showOnce];
    done();
    QuitAfter(0.3);
}

@end

// NSApplication.delegate is a weak reference; hold the delegate here or ARC
// releases it before the first callback.
static AppDelegate *gDelegate;

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        gDelegate = [AppDelegate new];
        app.delegate = gDelegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];  // no Dock icon
        [app run];
    }
    return 0;
}
