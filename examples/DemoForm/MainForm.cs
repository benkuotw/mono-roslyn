using System;
using System.Windows.Forms;

namespace DemoForm
{
    public partial class MainForm : Form
    {
        public MainForm()
        {
            InitializeComponent();
            pbLogo.Image = Properties.Resources.Logo;
            txtUser.Text = Properties.Settings.Default.UserName;
            tsslStatus.Text = "Built for " + AppDomain.CurrentDomain.SetupInformation.TargetFrameworkName
                + " | running on CLR " + Environment.Version + " | " + (Environment.Is64BitProcess ? "64-bit" : "32-bit");
        }

        private void btnSave_Click(object sender, EventArgs e)
        {
            Properties.Settings.Default.UserName = txtUser.Text;
            Properties.Settings.Default.Save();
            tsslStatus.Text = "Saved " + txtUser.Text;
        }

        private void tsbRefresh_Click(object sender, EventArgs e)
        {
            txtUser.Text = Properties.Settings.Default.UserName;
        }
    }
}
