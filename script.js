document.addEventListener('DOMContentLoaded', () => {
    const fadeEls = document.querySelectorAll('.fade-in');

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, {
        threshold: 0.15
    });

    fadeEls.forEach(el => observer.observe(el));

    const contactLink = document.getElementById('contact-link');
    if (contactLink) {
        const user = 'contact';
        const domain = 'finndigital.com';
        contactLink.addEventListener('click', (e) => {
            e.preventDefault();
            window.location.href = 'mailto:' + user + '@' + domain;
        });
    }
});
