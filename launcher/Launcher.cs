using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
[assembly: AssemblyTitle("Joker's eye")]
[assembly: AssemblyProduct("Joker's eye")]
[assembly: AssemblyDescription("Gotham City research workspace")]
[assembly: AssemblyVersion("1.3.1.0")]

class Launcher {
    public const string AppId = "JokersEye.Desktop";
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)] static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern IntPtr FindWindow(string cls, string title);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [STAThread] static void Main(string[] args) {
        if (args.Length == 2 && args[0] == "--register-shortcut") {
            TaskbarIdentity.ApplyShortcut(args[1]);
            return;
        }
        bool first;
        using (var mutex = new Mutex(true, @"Local\JokersEye.Desktop", out first)) {
            if (!first) {
                for (int i=0; i<30; i++) {
                    IntPtr window = FindWindow(null, "Joker's eye");
                    if (window != IntPtr.Zero) { if (IsIconic(window)) ShowWindow(window, 9); SetForegroundWindow(window); break; }
                    Thread.Sleep(100);
                }
                return;
            }
            try {
                Marshal.ThrowExceptionForHR(SetCurrentProcessExplicitAppUserModelID(AppId));
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new DesktopWindow(args.Length == 2 && args[0] == "--diagnostics" ? args[1] : null));
            } catch (Exception ex) { MessageBox.Show(ex.Message, "Joker's eye — 起動エラー", MessageBoxButtons.OK, MessageBoxIcon.Error); }
            finally { mutex.ReleaseMutex(); }
        }
    }
}
class Session { public string url { get; set; } public string token { get; set; } }

class DesktopWindow : Form {
    readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    readonly string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "JokersEye");
    readonly string diagnostics;
    readonly WebView2 view = new WebView2();
    readonly HttpClient client = new HttpClient(new HttpClientHandler { UseProxy=false });
    readonly System.Windows.Forms.Timer heartbeat = new System.Windows.Forms.Timer();
    Process server;
    SourceBrowser sourceBrowser;
    Session session;
    bool closing, closeReady, diagnosticStarted;
    public DesktopWindow(string diagnosticPath) {
        diagnostics = diagnosticPath;
        Text = "Joker's eye";
        Size = new Size(1380, 920);
        MinimumSize = new Size(900, 650);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        BackColor = Color.FromArgb(15, 21, 25);
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = BackColor;
        Controls.Add(view);
        client.Timeout = TimeSpan.FromSeconds(3);
        heartbeat.Interval = 20000;
        heartbeat.Tick += async delegate { if (!closing && session != null) { try { await Post("heartbeat"); } catch { } } };
        FormClosing += CloseHost;
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        TaskbarIdentity.Apply(Handle, Application.ExecutablePath);
    }
    protected override async void OnShown(EventArgs e) {
        base.OnShown(e);
        try {
            string python = Path.Combine(root, "runtime", "pythonw.exe"), app = Path.Combine(root, "app", "server.py");
            if (!File.Exists(python) || !File.Exists(app)) throw new IOException("ZIPをすべて展開してから起動してください。runtimeとappフォルダーが必要です。");
            CoreWebView2Environment.GetAvailableBrowserVersionString();
            Directory.CreateDirectory(data);
            string statefile = Path.Combine(data, "desktop-session-"+Process.GetCurrentProcess().Id+".json");
            server = Process.Start(new ProcessStartInfo(python, "\""+app+"\" --port 0 --session \""+statefile+"\"") { WorkingDirectory=root, UseShellExecute=false, CreateNoWindow=true });
            var json = new JavaScriptSerializer();
            for (int attempt=0; attempt<150; attempt++) {
                if (server.HasExited) throw new IOException("ローカルサービスが終了しました。application.logを確認してください。");
                if (File.Exists(statefile)) {
                    try {
                        session = json.Deserialize<Session>(File.ReadAllText(statefile));
                        client.DefaultRequestHeaders.Remove("X-Joker-Token");
                        client.DefaultRequestHeaders.Add("X-Joker-Token", session.token);
                        using (var response = await client.GetAsync(session.url+"api/health")) { if (response.IsSuccessStatusCode) break; }
                        session = null;
                    } catch (IOException) { session = null; } catch (HttpRequestException) { session = null; }
                }
                await Task.Delay(100);
            }
            if (session == null) throw new TimeoutException("ローカルサービスを起動できませんでした。アプリを開き直してください。");
            var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(data, "WebView2"));
            await view.EnsureCoreWebView2Async(env);
            view.CoreWebView2.Settings.AreDevToolsEnabled = diagnostics != null;
            view.CoreWebView2.Settings.IsStatusBarEnabled = false;
            view.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs request) { request.Handled = true; OpenExternal(request.Uri); };
            view.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs request) {
                Uri target;
                if (Uri.TryCreate(request.Uri, UriKind.Absolute, out target) && target.GetLeftPart(UriPartial.Authority) != new Uri(session.url).GetLeftPart(UriPartial.Authority)) { request.Cancel = true; OpenExternal(request.Uri); }
            };
            view.CoreWebView2.WebMessageReceived += delegate(object sender, CoreWebView2WebMessageReceivedEventArgs message) {
                if (message.Source.StartsWith(session.url, StringComparison.Ordinal) && message.TryGetWebMessageAsString() == "close") Close();
            };
            view.CoreWebView2.NavigationCompleted += async delegate {
                if (diagnostics == null || diagnosticStarted) return;
                diagnosticStarted = true;
                await Task.Delay(1500);
                string page = await view.ExecuteScriptAsync("JSON.stringify({connection:document.querySelector('#connection').textContent,version:document.querySelector('.rail-bottom small').textContent,title:document.title})");
                File.WriteAllText(diagnostics, "{\"page\":"+page+",\"taskbar\":"+TaskbarIdentity.Read(Handle)+"}");
                await view.ExecuteScriptAsync("document.querySelector('[data-page=map]').click()");
                await Task.Delay(1500);
                string map = await view.ExecuteScriptAsync("JSON.stringify({grid:!!document.querySelector('.machine-seat-grid'),seats:document.querySelectorAll('.machine-seat').length,physical:!!document.querySelector('.physical-floor-svg')})");
                File.WriteAllText(diagnostics+".map.json", map);
                await view.ExecuteScriptAsync("document.querySelector('.machine-seat-grid')?.scrollIntoView({block:'start'})");
                using (var output = File.Create(diagnostics+".png")) await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, output);
            };
            heartbeat.Start();
            sourceBrowser=new SourceBrowser(this,client,session.url,data);
            view.CoreWebView2.Navigate(session.url+"#"+session.token);
        } catch (Exception ex) {
            if (closing) return;
            Directory.CreateDirectory(data);
            File.AppendAllText(Path.Combine(data, "desktop.log"), DateTime.UtcNow.ToString("o")+" "+ex.GetType().Name+": "+ex.Message+Environment.NewLine);
            MessageBox.Show(this, ex.Message+"\n\nWebView2 Runtimeがない場合はMicrosoft公式サイトからインストールしてください。", "Joker's eye — 起動エラー", MessageBoxButtons.OK, MessageBoxIcon.Error);
            Close();
        }
    }
    static void OpenExternal(string address) {
        Uri target;
        if (Uri.TryCreate(address, UriKind.Absolute, out target) && (target.Scheme == "https" || target.Scheme == "http")) Process.Start(new ProcessStartInfo(address) { UseShellExecute=true });
    }
    async Task Post(string endpoint) {
        using (var response = await client.PostAsync(session.url+"api/"+endpoint, new StringContent("{}", System.Text.Encoding.UTF8, "application/json"))) response.EnsureSuccessStatusCode();
    }
    async void CloseHost(object sender, FormClosingEventArgs e) {
        if (closeReady) return;
        e.Cancel = true;
        if (closing) return;
        closing = true;
        heartbeat.Stop();
        if (sourceBrowser != null) sourceBrowser.Dispose();
        if (session != null) { try { await Post("shutdown"); } catch { } }
        if (server != null && !server.HasExited) await Task.Run(delegate { if (!server.WaitForExit(4000)) server.Kill(); });
        view.Dispose(); client.Dispose(); heartbeat.Dispose();
        if (server != null) server.Dispose();
        closeReady = true;
        Close();
    }
}

static class TaskbarIdentity {
    static readonly Guid Format = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
    [StructLayout(LayoutKind.Sequential)] struct PropertyKey { public Guid format; public uint id; public PropertyKey(uint n) { format=Format; id=n; } }
    [StructLayout(LayoutKind.Explicit, Size=24)] struct Variant { [FieldOffset(0)] public ushort type; [FieldOffset(8)] public IntPtr pointer; }
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface Store {
        [PreserveSig] int GetCount(out uint count);
        [PreserveSig] int GetAt(uint index, out PropertyKey key);
        [PreserveSig] int GetValue(ref PropertyKey key, out Variant value);
        [PreserveSig] int SetValue(ref PropertyKey key, ref Variant value);
        [PreserveSig] int Commit();
    }
    [DllImport("shell32.dll")] static extern int SHGetPropertyStoreForWindow(IntPtr window, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out Store store);
    [DllImport("shell32.dll", CharSet=CharSet.Unicode)] static extern int SHGetPropertyStoreFromParsingName(string path, IntPtr context, uint flags, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out Store store);
    [DllImport("ole32.dll")] static extern int PropVariantClear(ref Variant variant);
    static Store GetStore(IntPtr window) {
        Guid iid = typeof(Store).GUID; Store store;
        Marshal.ThrowExceptionForHR(SHGetPropertyStoreForWindow(window, ref iid, out store));
        return store;
    }
    static void Set(Store store, uint id, string text) {
        var key = new PropertyKey(id);
        var value = new Variant { type=31, pointer=Marshal.StringToCoTaskMemUni(text) };
        try { Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref value)); } finally { PropVariantClear(ref value); }
    }
    public static void Apply(IntPtr window, string executable) {
        Store store = GetStore(window);
        try { Set(store, 2, "\""+executable+"\""); Set(store, 3, executable+",0"); Set(store, 4, "Joker's eye"); Set(store, 5, Launcher.AppId); }
        finally { Marshal.ReleaseComObject(store); }
    }
    public static void ApplyShortcut(string path) {
        Guid iid = typeof(Store).GUID; Store store;
        Marshal.ThrowExceptionForHR(SHGetPropertyStoreFromParsingName(Path.GetFullPath(path), IntPtr.Zero, 2, ref iid, out store));
        try { Set(store, 5, Launcher.AppId); Marshal.ThrowExceptionForHR(store.Commit()); }
        finally { Marshal.ReleaseComObject(store); }
    }
    public static string Read(IntPtr window) {
        Store store = GetStore(window);
        var values = new System.Collections.Generic.Dictionary<string,string>();
        try { foreach (uint id in new uint[]{2,3,4,5}) {
            var key = new PropertyKey(id); Variant value;
            Marshal.ThrowExceptionForHR(store.GetValue(ref key, out value));
            values[id.ToString()] = value.type == 31 ? Marshal.PtrToStringUni(value.pointer) : "";
            PropVariantClear(ref value);
        } } finally { Marshal.ReleaseComObject(store); }
        return new JavaScriptSerializer().Serialize(values);
    }
}
