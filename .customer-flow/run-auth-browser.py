import os
import sys
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'crunchy_backend.settings'
os.environ['DATABASE_URL'] = 'sqlite:///db.sqlite3'
os.environ['CHANNEL_LAYER_TYPE'] = 'inmemory'
import django
django.setup()
from django.conf import settings
from django.db import connections
from django.test.utils import setup_databases, teardown_databases, override_settings
from django.test.testcases import LiveServerThread, _StaticFilesHandler
from apps.customer_web.auth import AuthThrottle, PhoneThrottle
from apps.user_accounts.models import User
from apps.restaurants.models import Restaurant, Branch
from apps.customer_web.models import CustomerProfile
from django.contrib.auth.hashers import make_password
frontend = Path(r'C:\Users\Suraj\Desktop\crunchybag')
spec = frontend / 'tests' / 'customer-auth-live.generated.spec.ts'
assert not spec.exists()
source = (frontend / 'tests' / 'customer.spec.ts').read_text(encoding='utf-8')
source = source[:source.index("test('cart rebases")]
source += r"""
async function realAuth(page:Page) {
  await page.route('**/api/v1/customer/auth/**', async route => {
    const response = await route.fetch({url:process.env.AUTH_TEST_BACKEND + new URL(route.request().url()).pathname});
    await route.fulfill({response});
  });
}
test('real Django: inactive blank-password guest activates after recovery OTP',async({page})=>{
  await setup(page); await realAuth(page); await openCheckout(page);
  await page.getByLabel('Mobile Phone Number',{exact:true}).fill('+977 9841234567');
  const started=page.waitForResponse(r=>r.url().endsWith('/customer/auth/recovery-start/'));
  await page.getByRole('button',{name:'Forgot PIN?',exact:true}).click();
  const otp=await (await started).json();
  expect(otp.next_action).toBe('signup');
  for(const [index,digit] of [...otp.demo_code].entries())await page.getByLabel(`Code digit ${index+1}`).fill(String(digit));
  await page.getByRole('button',{name:'Verify & Continue'}).click();
  await page.getByLabel('Full name',{exact:true}).fill('live_guest');
  await page.getByLabel('Set Password').fill('Crisp!River49Ocean');
  await page.getByLabel('New 4-Digit Quick PIN').fill('9274');
  const registered=page.waitForResponse(r=>r.url().endsWith('/customer/auth/register/'));
  await page.getByRole('button',{name:'Complete & Continue to Checkout'}).click();
  const response=await registered; expect(response.status()).toBe(201);
  expect((await response.json()).access).toBeTruthy();
  await expect(page.getByAltText('Merchant payment QR')).toBeVisible();
  for(const [method,credential] of [['PIN','9274'],['PASSWORD','Crisp!River49Ocean']]) {
    const login=await page.request.post(process.env.AUTH_TEST_BACKEND+'/api/v1/customer/auth/login/',{data:{phone:'9841234567',method,credential}});
    expect(login.status()).toBe(200);
  }
});
test('real Django: existing country-code account recovers password and PIN',async({page})=>{
  await setup(page); await realAuth(page); await openCheckout(page);
  await page.getByLabel('Mobile Phone Number',{exact:true}).fill('+977 9841234568');
  const started=page.waitForResponse(r=>r.url().endsWith('/customer/auth/recovery-start/'));
  await page.getByRole('button',{name:'Forgot PIN?',exact:true}).click();
  const otp=await (await started).json(); expect(otp.next_action).toBe('recovery');
  for(const [index,digit] of [...otp.demo_code].entries())await page.getByLabel(`Login code digit ${index+1}`).fill(String(digit));
  await page.getByRole('button',{name:'Verify Recovery Code',exact:true}).click();
  await page.getByLabel('New Password',{exact:true}).fill('New-Passphrase-8*Forest');
  await page.getByLabel('New 4-Digit Quick PIN',{exact:true}).fill('8351');
  const reset=page.waitForResponse(r=>r.url().endsWith('/customer/auth/reset/'));
  await page.getByRole('button',{name:'Save New Credentials & Sign In'}).click();
  expect((await reset).status()).toBe(200);
  for(const [method,credential] of [['PIN','8351'],['PASSWORD','New-Passphrase-8*Forest']]) {
    const login=await page.request.post(process.env.AUTH_TEST_BACKEND+'/api/v1/customer/auth/login/',{data:{phone:'9841234568',method,credential}});
    expect(login.status()).toBe(200);
  }
});
"""
with override_settings(CUSTOMER_DEMO_OTP=True, ALLOWED_HOSTS=['127.0.0.1','localhost','testserver']):
    old_config = setup_databases(verbosity=0, interactive=False)
    server = None
    try:
        owner = User.objects.create_user(username='live_owner', role='RESTAURANT_OWNER')
        org = Restaurant.objects.create(name='Browser test', admin=owner)
        Branch.objects.create(pk=1, restaurant=org, name='Web Outlet', branch_code='LIVE')
        guest = User.objects.create(username='guest', phone_number='9779841234567', password='', is_active=False)
        registered = User.objects.create_user(username='registered', phone_number='9779841234568', password='Old-secret88')
        CustomerProfile.objects.create(user=registered, pin_hash=make_password('4567'))
        AuthThrottle.rate = PhoneThrottle.rate = '1000/min'
        connection = connections['default']
        connection.inc_thread_sharing()
        server = LiveServerThread('127.0.0.1', _StaticFilesHandler, connections_override={'default': connection})
        server.start(); server.is_ready.wait()
        if server.error: raise server.error
        spec.write_text(source,encoding='utf-8')
        env = dict(os.environ, AUTH_TEST_BACKEND=f'http://127.0.0.1:{server.port}')
        result = subprocess.run(['npx.cmd','playwright','test','tests/customer-auth-live.generated.spec.ts'],cwd=frontend,env=env)
        if result.returncode: raise RuntimeError('Live browser checks failed')
        guest.refresh_from_db(); registered.refresh_from_db()
        assert guest.is_active and guest.username == 'live_guest' and guest.check_password('Crisp!River49Ocean')
        assert registered.check_password('New-Passphrase-8*Forest')
        print('Database verified: original guest activated and existing account recovered.',flush=True)
    finally:
        if server: server.terminate(); server.join()
        if spec.exists(): spec.unlink()
        teardown_databases(old_config,verbosity=0)
