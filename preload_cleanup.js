/**
 * Preload cookie suppression and HCP bypass script for Playwright page.add_init_script.
 * Injected before any page scripts or DOM nodes execute.
 */
(function () {
    'use strict';

    // 1. Mock CMP globals immediately to stop banners from even bootstrapping
    try {
        window.OneTrustActiveGroups = ',C0001,C0002,C0003,C0004,';
        window.OnetrustActiveGroups = ',C0001,C0002,C0003,C0004,';

        // Mock OneTrust API so inline scripts checking it don't throw or trigger banner UI
        window.OneTrust = window.OneTrust || {
            IsAlertBoxClosed: () => true,
            GetDomainData: () => ({ ShowAlertNotice: false }),
            OnConsentChanged: () => { },
            Close: () => { }
        };

        // Common pharma HCP global gate flags
        window.isHCP = true;
        window.__HCP_VERIFIED__ = true;
        window.HCP_ACCEPTED = true;
    } catch (e) {
        // Ignore if frozen or sandboxed
    }

    const suppressCookiesAndStorage = () => {
        const hostname = window.location.hostname;
        // On initial add_init_script run, hostname can be empty (about:blank)
        if (!hostname) return;

        const parts = hostname.split('.');
        // Corrected multi-part TLD detection (.co.uk, .com.au, etc.)
        const isMultiPartTld = parts.length > 2 && (parts[parts.length - 2] === 'co' || parts[parts.length - 2] === 'com');
        const rootDomain = parts.length > 2
            ? (isMultiPartTld ? parts.slice(-3).join('.') : parts.slice(-2).join('.'))
            : hostname;

        const securePart = window.location.protocol === 'https:' ? '; Secure' : '';
        const cookieDomainPart = '; domain=.' + rootDomain;
        const cookieSuffix = '; path=/; max-age=31536000' + securePart + '; SameSite=Lax';

        const consentDate = new Date().toISOString();
        const cookies = [
            // OneTrust / Optanon
            'OptanonAlertBoxClosed=' + consentDate,
            'OptanonConsent=isGpcEnabled=0&datestamp=' + encodeURIComponent(new Date().toUTCString()) + '&version=6.37.0&landingPath=NotLandingPage&groups=C0001%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1',
            'eupconsent-v2=CP0123456789',
            // Generic Cookie Consents
            'cookie_consent_accepted=true',
            'has_seen_cookie_banner=1',
            'cookieconsent_status=dismiss',
            'terms_accepted=true',
            // Healthcare Professional (HCP) Gates
            'hcp-gate=true',
            'isHCP=1',
            'is_hcp=true',
            'hcp_acknowledged=1',
            'HCPVerified=true',
            'hcp_verified=1',
            'user_type=hcp',
            'profession=physician'
        ];

        try {
            for (let i = 0; i < cookies.length; i++) {
                // Set with wildcard domain (.domain.com)
                document.cookie = cookies[i] + cookieDomainPart + cookieSuffix;
                // Set host-only cookie (fallback for localhost or stricter paths)
                document.cookie = cookies[i] + cookieSuffix;
            }
        } catch (err) {
            // Document cookie write may fail in cross-origin frames
        }

        try {
            // LocalStorage suppression
            window.localStorage.setItem('OptanonAlertBoxClosed', consentDate);
            window.localStorage.setItem('hcp-gate-dismissed', 'true');
            window.localStorage.setItem('isHCP', 'true');
            window.localStorage.setItem('is_hcp', 'true');
            window.localStorage.setItem('hcp_verified', 'true');
            window.localStorage.setItem('cookieConsentAccepted', 'true');
            window.localStorage.setItem('cookie-consent', 'accepted');

            // SessionStorage suppression
            window.sessionStorage.setItem('hcp-modal-shown', 'true');
            window.sessionStorage.setItem('hcp_verified', 'true');
            window.sessionStorage.setItem('gate_passed', 'true');
        } catch (e) {
            // Storage access might be restricted
        }
    };

    // Run as early as possible
    suppressCookiesAndStorage();

    // Re-run on document states to catch late URL assignments or storage wipes
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', suppressCookiesAndStorage);
    } else {
        suppressCookiesAndStorage();
    }
})();