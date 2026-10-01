using System;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Visible ordinary public-source browser. Its profile is isolated from the
// app origin. Source authentication cookies/tokens are never exported.
class SourceBrowser : IDisposable {
    readonly Form owner;
    readonly HttpClient client;
    readonly string baseUrl, folder;
    readonly Timer timer = new Timer();
    readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength=10*1024*1024 };
    Form window;
    WebView2 browser;
    bool busy, disposed;
    string lastTask;
    string navigationStatus="not-started";
    int statusCode;
    bool accessBlocked;
    void Log(string message) {
        try { File.AppendAllText(Path.Combine(folder,"source-browser.log"),DateTime.UtcNow.ToString("o")+" "+message+Environment.NewLine); } catch(IOException) { }
    }
    public SourceBrowser(Form owner, HttpClient client, string baseUrl, string folder) {
        this.owner=owner; this.client=client; this.baseUrl=baseUrl; this.folder=folder;
        timer.Interval=750; timer.Tick+=async delegate { await Poll(); }; timer.Start();
    }
    async Task EnsureBrowser() {
        if (browser != null) return;
        window=new Form { Text="Joker's eye · 公開データ取得ブラウザー",Size=new Size(1000,750),StartPosition=FormStartPosition.CenterParent,Icon=owner.Icon };
        browser=new WebView2 { Dock=DockStyle.Fill };
        window.Controls.Add(browser);
        window.FormClosing+=async delegate(object sender,FormClosingEventArgs e) {
            if (!disposed) { e.Cancel=true; window.Hide(); await Send("scrape/stop",new { }); }
        };
        var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(folder,"SourceWebView2"));
        window.Show(owner);
        await browser.EnsureCoreWebView2Async(env);
        browser.CoreWebView2.Settings.AreDevToolsEnabled=false;
        browser.CoreWebView2.NewWindowRequested+=delegate(object s,CoreWebView2NewWindowRequestedEventArgs e){e.Handled=true;};
        browser.CoreWebView2.PermissionRequested+=delegate(object s,CoreWebView2PermissionRequestedEventArgs e){e.State=CoreWebView2PermissionState.Deny;};
        browser.CoreWebView2.NavigationStarting+=delegate(object s,CoreWebView2NavigationStartingEventArgs e){
            Uri url;if(!Uri.TryCreate(e.Uri,UriKind.Absolute,out url)||url.Scheme!="https"||url.Host!="min-repo.com")e.Cancel=true;
            if(!e.Cancel)Log("navigation-start "+e.Uri);
        };
        browser.CoreWebView2.NavigationCompleted+=delegate(object s,CoreWebView2NavigationCompletedEventArgs e){
            navigationStatus="HTTP "+e.HttpStatusCode+" / "+e.WebErrorStatus+" / success="+e.IsSuccess;
            statusCode=e.HttpStatusCode;
            accessBlocked=statusCode==401||statusCode==403||statusCode==429;
            Log("navigation-completed "+navigationStatus);
        };
        browser.CoreWebView2.WebResourceResponseReceived+=delegate(object s,CoreWebView2WebResourceResponseReceivedEventArgs e){
            Uri uri;if(Uri.TryCreate(e.Request.Uri,UriKind.Absolute,out uri)&&uri.Host=="min-repo.com"&&uri.AbsolutePath=="/wp-admin/admin-ajax.php")
                Log("browser-page-script-response HTTP "+e.Response.StatusCode);
        };
    }
    async Task Send(string path, object payload) {
        using(var body=new StringContent(json.Serialize(payload),Encoding.UTF8,"application/json"))
        using(var response=await client.PostAsync(baseUrl+"api/"+path,body)){response.EnsureSuccessStatusCode();}
    }
    async Task Poll() {
        if (busy||disposed)return;
        busy=true;
        string id=null, error=null;
        try {
            string taskText=await client.GetStringAsync(baseUrl+"api/scrape/browser-task");
            var task=json.Deserialize<System.Collections.Generic.Dictionary<string,object>>(taskText);
            if (!task.ContainsKey("id"))return;
            id=(string)task["id"];
            if (id==lastTask)return;
            lastTask=id;
            string url=(string)task["url"];
            Uri target;if(!Uri.TryCreate(url,UriKind.Absolute,out target)||target.Scheme!="https"||target.Host!="min-repo.com")throw new Exception("公開取得元のURLではありません。");
            await EnsureBrowser();
            if (disposed)return;
            if(!window.Visible)window.Show(owner);
            navigationStatus="loading";
            statusCode=0;accessBlocked=false;
            // Follow a genuine link in the currently rendered public page when
            // available. This preserves ordinary browser navigation/referrers;
            // no headers, cookies or source-site checks are manufactured.
            string followed=await browser.ExecuteScriptAsync("(()=>{const target=new URL("+json.Serialize(url)+",location.href).href;const link=Array.from(document.querySelectorAll('a[href]')).find(a=>a.href===target&&!a.target);if(!link)return false;link.click();return true;})()");
            if(followed!="true")browser.CoreWebView2.Navigate(url);
            string html=null;
            // Let ordinary page loading finish. Never act on CAPTCHA,
            // authentication, paywall or other access-restriction interfaces.
            for(int i=0;i<110&&!disposed;i++) {
                await Task.Delay(500);
                if(accessBlocked)throw new Exception("公開サイトが取得を制限しました（HTTP "+statusCode+"）。再試行せず停止します。");
                if(browser.Source==null||Uri.Compare(browser.Source,target,UriComponents.HttpRequestUrl,UriFormat.UriEscaped,StringComparison.OrdinalIgnoreCase)!=0)continue;
                string ready=await browser.ExecuteScriptAsync("!!document.querySelector('h1') && !!document.querySelector('table') && document.readyState !== 'loading'");
                if(ready=="true") {
                    html=json.Deserialize<string>(await browser.ExecuteScriptAsync("document.documentElement.outerHTML"));break;
                }
                // A successful but genuinely empty document is a transient
                // response, not usable data. Retry once by ordinary Reload.
                // Never retry an HTTP access/rate restriction or confirmation UI.
                if(i==12&&statusCode==200) {
                    string empty=await browser.ExecuteScriptAsync("document.readyState === 'complete' && document.scripts.length === 0 && !!document.body && document.body.innerHTML.trim() === ''");
                    if(empty=="true") {
                        Log("empty-document ordinary-reload-once "+url);
                        await Task.Delay(3000);
                        browser.CoreWebView2.Reload();
                    }
                }
            }
            if (disposed)return;
            if(html==null) {
                string snapshot=await browser.ExecuteScriptAsync("({ready:document.readyState,title:document.title,headings:document.querySelectorAll('h1').length,tables:document.querySelectorAll('table').length,textLength:document.body?document.body.innerText.length:0,browserCheck:Array.from(document.scripts).some(s=>s.textContent.includes('w_scd_n'))})");
                Log("unreadable-page "+url+" "+navigationStatus+" "+snapshot);
                throw new Exception("公開ページを表示できませんでした（"+navigationStatus+"）。未掲載・閲覧制限・ブラウザー確認を確認してください。制限の回避は行いません。");
            }
            await Send("scrape/browser-result",new {id=id,html=html});
        } catch(Exception ex) {
            error=ex.Message;
        } finally {busy=false;}
        if(error!=null&&id!=null&&!disposed)try{await Send("scrape/browser-result",new {id=id,error=error});}catch{}
    }
    public void Dispose() {
        disposed=true;timer.Stop();timer.Dispose();
        if(window!=null){window.Close();window.Dispose();}
    }
}
